import FitnessCore
import Foundation
import Observation

/// Gyms and per-gym machines: lists are cached raw on disk and shown at once (also offline), refreshed in the
/// background. Every write needs the network and is never queued.
@MainActor
@Observable
final class GymRepository: RemoteRepository {
    private(set) var list: GymListDTO?
    private(set) var listFetchedAt: Date?
    private(set) var machines: [Int: GymMachinesDTO] = [:]
    private(set) var machinesFetchedAt: [Int: Date] = [:]
    private(set) var isRefreshing = false
    private(set) var failure: String?
    /// The last refresh could not reach the server (what is on screen is the cached copy).
    private(set) var isOffline = false
    /// Machines still to set up at the gym that was just switched to (from `PUT /gyms/current`).
    private(set) var lastUnmappedCount: Int?

    @ObservationIgnored private var lastNamespace: String?
    /// Called after a write that changes the plan (gym switch, machine saved): re-plans (sync).
    @ObservationIgnored var afterPlanChange: (() async -> Void)?

    init(settings: SettingsStore, network: NetworkMonitor, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool = { false }) {
        super.init(settings: settings, network: network, store: store, isDemo: isDemo)
    }

    // MARK: State

    /// The gym the app uses: the server's value, else the one stored locally (offline).
    var currentGymId: Int? { GymSync.resolve(local: appSettings.currentGymId, server: list?.currentGymId) }

    /// Program machines still to set up at the current gym: the server's answer to the last switch, then the loaded list.
    var unmappedCount: Int? {
        if let lastUnmappedCount { return lastUnmappedCount }
        return currentGymId.flatMap { machines[$0] }.map { MachineLogic.progress($0.items).remaining }
    }

    func gym(_ id: Int?) -> GymSummaryDTO? { id.flatMap { id in list?.gyms.first { $0.id == id } } }

    func machineList(_ gymId: Int) -> GymMachinesDTO? { machines[gymId] }

    // MARK: Loading

    private func syncNamespace() {
        let ns = namespace
        if lastNamespace != ns {
            resetState()
            lastNamespace = ns
        }
    }

    /// Cache first (immediately), then the network.
    func loadList() async {
        syncNamespace()
        if list == nil { await readListCache() }
        guard canFetch else { return }
        await fetchList()
    }

    func loadMachines(gymId: Int) async {
        syncNamespace()
        if machines[gymId] == nil { await readMachinesCache(gymId) }
        guard canFetch else { return }
        await fetchMachines(gymId: gymId)
    }

    func fetchList() async {
        guard let transport = transport() else { return }
        let ns = namespace
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let data = try await transport.get(path: GymEndpoints.gyms)
            let dto = try await decode(GymListDTO.self, data)
            guard ns == namespace else { return }
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: "gyms")
            list = dto
            listFetchedAt = .now
            adoptServerGym(dto.currentGymId)
            failure = nil; isOffline = false
        } catch { noteFailure(error) }
    }

    func fetchMachines(gymId: Int) async {
        guard let transport = transport() else { return }
        let ns = namespace
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let data = try await transport.get(path: GymEndpoints.machines(gymId))
            let dto = try await decode(GymMachinesDTO.self, data)
            guard ns == namespace else { return }
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: Self.machinesKey(gymId))
            machines[gymId] = dto
            machinesFetchedAt[gymId] = .now
            if gymId == currentGymId { lastUnmappedCount = MachineLogic.progress(dto.items).remaining }
            failure = nil; isOffline = false
        } catch { noteFailure(error) }
    }

    private func noteFailure(_ error: Error) {
        guard let f = RemoteRepository.failure(for: error) else { return }
        failure = f.message; isOffline = f.offline
    }

    private func decode<T: Decodable & Sendable>(_ type: T.Type, _ data: Data) async throws -> T {
        do { return try await Self.decode(T.self, from: data) }
        catch { throw APIError.decoding(error) }
    }

    /// The server's current gym wins over the locally stored one (kept for offline use).
    private func adoptServerGym(_ id: Int?) {
        let resolved = GymSync.resolve(local: appSettings.currentGymId, server: id)
        if resolved != appSettings.currentGymId { appSettings.currentGymId = resolved }
    }

    private static func machinesKey(_ gymId: Int) -> String { "gym/\(gymId)/machines" }

    private func readListCache() async {
        let ns = namespace
        guard let e = store.read(namespace: ns, key: "gyms"), let dto = try? await Self.decode(GymListDTO.self, from: e.data),
              ns == namespace, list == nil else { return }
        list = dto; listFetchedAt = e.fetchedAt
    }

    private func readMachinesCache(_ gymId: Int) async {
        let ns = namespace
        guard let e = store.read(namespace: ns, key: Self.machinesKey(gymId)),
              let dto = try? await Self.decode(GymMachinesDTO.self, from: e.data),
              ns == namespace, machines[gymId] == nil else { return }
        machines[gymId] = dto; machinesFetchedAt[gymId] = e.fetchedAt
    }

    private func resetState() {
        list = nil; listFetchedAt = nil; machines = [:]; machinesFetchedAt = [:]
        failure = nil; isOffline = false; lastUnmappedCount = nil
    }

    /// Backend or data was reset: forget everything (the next load starts clean).
    func reset() {
        store.removeAll()
        resetState()
        lastNamespace = nil
    }

    // MARK: Writes (online only, never queued)

    private func write(_ method: String, _ path: String, body: Encodable?) async throws -> Data {
        guard canWrite else { throw appSettings.isConfigured ? GymError.needsConnection : GymError.notConfigured }
        guard let transport = transport() else { throw GymError.notConfigured }
        let data = try body.map { try APICoding.makeEncoder().encode(AnyEncodable($0)) }
        do { return try await transport.data(method: method, path: path, query: [], body: data) }
        catch { throw GymError.map(error) }
    }

    /// `PUT /gyms/current`, then re-plan (sync). Returns how many program machines still need setup.
    @discardableResult
    func switchGym(to id: Int) async throws -> Int {
        let data = try await write("PUT", GymEndpoints.current, body: GymCurrentRequest(gymId: id))
        let response = try decodeWrite(GymCurrentResponse.self, data)
        appSettings.currentGymId = response.currentGymId ?? id
        lastUnmappedCount = response.unmappedCount ?? 0
        await fetchList()
        await afterPlanChange?()
        return response.unmappedCount ?? 0
    }

    @discardableResult
    func addGym(name: String) async throws -> GymSummaryDTO {
        let data = try await write("POST", GymEndpoints.gyms, body: GymNameRequest(name: name.trimmingCharacters(in: .whitespacesAndNewlines)))
        let gym = try decodeWrite(GymSummaryDTO.self, data)
        await fetchList()
        return gym
    }

    func renameGym(id: Int, to name: String) async throws {
        _ = try await write("PATCH", GymEndpoints.gym(id), body: GymNameRequest(name: name.trimmingCharacters(in: .whitespacesAndNewlines)))
        await fetchList()
    }

    /// 409 (has sessions / is current) comes back as `.conflict` with the server's message.
    func deleteGym(id: Int) async throws {
        _ = try await write("DELETE", GymEndpoints.gym(id), body: nil)
        machines[id] = nil
        await fetchList()
    }

    /// Creates the machine (new variant) or patches the existing one; refreshes the list and re-plans.
    @discardableResult
    func saveMachine(gymId: Int, item: MachineItemDTO, draft: MachineDraft) async throws -> MachineItemDTO? {
        if let variant = item.variant {
            let patch = VariantPatchRequest(name: draft.trimmedName, weightLadder: draft.ladder, notes: draft.notes.trimmedOrNil, active: true)
            _ = try await write("PATCH", GymEndpoints.variant(variant.id), body: patch)
        } else {
            let create = MachineCreateRequest(exerciseId: item.exerciseId, name: draft.trimmedName, equipmentType: draft.equipmentType,
                                              weightLadder: draft.ladder.isEmpty ? nil : draft.ladder, notes: draft.notes.trimmedOrNil)
            _ = try await write("POST", GymEndpoints.machines(gymId), body: create)
        }
        await fetchMachines(gymId: gymId)
        await fetchList()
        await afterPlanChange?()
        return machines[gymId]?.items.first { $0.exerciseId == item.exerciseId }
    }

    private func decodeWrite<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try APICoding.makeDecoder().decode(T.self, from: data) }
        catch { throw GymError.failed(APIError.message(for: APIError.decoding(error))) }
    }

    // MARK: Ladder preview

    /// Server preview when online, the local rule (same result) otherwise or on any failure.
    func previewLadder(start: Double, step: Double, count: Int, extra: [Double] = []) async -> [Double] {
        let local = LadderRules.generate(start: start, step: step, count: count, extra: extra)
        guard canWrite, let transport = transport() else { return local }
        do {
            let body = try APICoding.makeEncoder().encode(LadderPreviewRequest(start: start, step: step, count: count, extra: extra.isEmpty ? nil : extra))
            let data = try await transport.perform(method: "POST", path: GymEndpoints.ladderPreview, body: body)
            let ladder = try APICoding.makeDecoder().decode(LadderPreviewResponse.self, from: data).weightLadder
            return ladder.isEmpty ? local : ladder
        } catch { return local }
    }
}
