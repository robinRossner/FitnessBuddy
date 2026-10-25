import Foundation
import Observation

/// Every gym endpoint the app uses. The only place that knows the paths.
enum GymEndpoints {
    static let gyms = "/api/v1/gyms"
    static let current = "/api/v1/gyms/current"
    static let ladderPreview = "/api/v1/weight-ladder/preview"
    static func gym(_ id: Int) -> String { gyms + "/\(id)" }
    static func machines(_ gymId: Int) -> String { gym(gymId) + "/machines" }
    static func variant(_ id: Int) -> String { "/api/v1/variants/\(id)" }
}

/// Failure of a gym action, in words for the screen.
enum GymError: LocalizedError, Equatable {
    case needsConnection
    case notConfigured
    /// 409: duplicate name, gym with sessions, current gym, machine already exists.
    case conflict(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .needsConnection: "Needs a connection"
        case .notConfigured: "Connect your backend in Settings first"
        case .conflict(let m), .failed(let m): m
        }
    }

    static func map(_ error: Error) -> GymError {
        if let e = error as? GymError { return e }
        if let api = error as? APIError {
            switch api {
            case .transport: return .needsConnection
            case .notConfigured: return .notConfigured
            case .http(409, let body): return .conflict(CoachError.detail(body) ?? "This conflicts with existing data.")
            case .http(let code, let body): return .failed(CoachError.detail(body) ?? "Server error \(code)")
            default: return .failed(api.errorDescription ?? "Something went wrong")
            }
        }
        return .failed(error.localizedDescription)
    }
}

/// Gyms and per-gym machines: lists are cached raw on disk and shown at once (also offline), refreshed in the
/// background. Every write needs the network and is never queued.
@MainActor
@Observable
final class GymRepository {
    typealias Transport = @Sendable (_ method: String, _ path: String, _ query: [URLQueryItem], _ body: Data?) async throws -> Data

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

    @ObservationIgnored private let store: any StatsCacheStore
    @ObservationIgnored private let appSettings: SettingsStore
    @ObservationIgnored private let network: NetworkMonitor
    @ObservationIgnored private let isDemo: @MainActor () -> Bool
    @ObservationIgnored private var lastNamespace: String?
    #if DEBUG
    @ObservationIgnored var demoTransport: Transport?
    #endif
    /// Tests inject a fake transport here.
    @ObservationIgnored var transportOverride: Transport?
    /// Called after a write that changes the plan (gym switch, machine saved): re-plans (sync).
    @ObservationIgnored var afterPlanChange: (() async -> Void)?

    init(settings: SettingsStore, network: NetworkMonitor, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool = { false }) {
        self.appSettings = settings
        self.network = network
        self.store = store
        self.isDemo = isDemo
    }

    // MARK: State

    var namespace: String { isDemo() ? "demo" : (appSettings.fingerprint ?? "unconfigured") }
    var canFetch: Bool { transport() != nil }

    /// Writes need a reachable server (the demo answers locally).
    var canWrite: Bool { isDemo() || (appSettings.isConfigured && network.isConnected) }

    var writeBlockedHint: String? {
        if canWrite { return nil }
        return appSettings.isConfigured ? "Needs a connection" : "Connect your backend in Settings"
    }

    /// The gym the app uses: the server's value, else the one stored locally (offline).
    var currentGymId: Int? { GymSync.resolve(local: appSettings.currentGymId, server: list?.currentGymId) }

    /// Program machines still to set up at the current gym: the server's answer to the last switch, then the loaded list.
    var unmappedCount: Int? {
        if let lastUnmappedCount { return lastUnmappedCount }
        return currentGymId.flatMap { machines[$0] }.map { MachineLogic.progress($0.items).remaining }
    }

    func gym(_ id: Int?) -> GymSummaryDTO? { id.flatMap { id in list?.gyms.first { $0.id == id } } }

    func machineList(_ gymId: Int) -> GymMachinesDTO? { machines[gymId] }

    // MARK: Transport

    private func transport() -> Transport? {
        if let transportOverride { return transportOverride }
        #if DEBUG
        if isDemo(), let demoTransport { return demoTransport }
        #endif
        guard !isDemo(), appSettings.isConfigured, let client = appSettings.makeClient() else { return nil }
        return { method, path, query, body in try await client.perform(method: method, path: path, query: query, body: body) }
    }

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
        if list == nil { readListCache() }
        guard canFetch else { return }
        await fetchList()
    }

    func loadMachines(gymId: Int) async {
        syncNamespace()
        if machines[gymId] == nil { readMachinesCache(gymId) }
        guard canFetch else { return }
        await fetchMachines(gymId: gymId)
    }

    func fetchList() async {
        guard let transport = transport() else { return }
        let ns = namespace
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let data = try await transport("GET", GymEndpoints.gyms, [], nil)
            let dto = try decode(GymListDTO.self, data)
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
            let data = try await transport("GET", GymEndpoints.machines(gymId), [], nil)
            let dto = try decode(GymMachinesDTO.self, data)
            guard ns == namespace else { return }
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: Self.machinesKey(gymId))
            machines[gymId] = dto
            machinesFetchedAt[gymId] = .now
            if gymId == currentGymId { lastUnmappedCount = MachineLogic.progress(dto.items).remaining }
            failure = nil; isOffline = false
        } catch { noteFailure(error) }
    }

    private func noteFailure(_ error: Error) {
        if Task.isCancelled || (error as? APIError)?.isCancellation == true || error is CancellationError { return }
        failure = APIError.message(for: error)
        if case APIError.transport = error { isOffline = true } else { isOffline = false }
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try APICoding.makeDecoder().decode(T.self, from: data) }
        catch { throw APIError.decoding(error) }
    }

    /// The server's current gym wins over the locally stored one (kept for offline use).
    private func adoptServerGym(_ id: Int?) {
        let resolved = GymSync.resolve(local: appSettings.currentGymId, server: id)
        if resolved != appSettings.currentGymId { appSettings.currentGymId = resolved }
    }

    private static func machinesKey(_ gymId: Int) -> String { "gym/\(gymId)/machines" }

    private func readListCache() {
        if let e = store.read(namespace: namespace, key: "gyms"), let dto = try? APICoding.makeDecoder().decode(GymListDTO.self, from: e.data) {
            list = dto; listFetchedAt = e.fetchedAt
        }
    }

    private func readMachinesCache(_ gymId: Int) {
        if let e = store.read(namespace: namespace, key: Self.machinesKey(gymId)),
           let dto = try? APICoding.makeDecoder().decode(GymMachinesDTO.self, from: e.data) {
            machines[gymId] = dto; machinesFetchedAt[gymId] = e.fetchedAt
        }
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
        do { return try await transport(method, path, [], data) }
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
            let data = try await transport("POST", GymEndpoints.ladderPreview, [], body)
            let ladder = try APICoding.makeDecoder().decode(LadderPreviewResponse.self, from: data).weightLadder
            return ladder.isEmpty ? local : ladder
        } catch { return local }
    }
}

/// What the machine editor sends.
struct MachineDraft: Equatable {
    var name: String
    var equipmentType: String
    var notes: String
    var ladder: [Double]

    var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// A save is allowed with a name, and (for stack machines) at least one step.
    var isValid: Bool { !trimmedName.isEmpty && (!MachineLogic.needsLadder(equipmentType) || !ladder.isEmpty) }
}

extension String {
    var trimmedOrNil: String? {
        let t = trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}

/// Type-erases an `Encodable` so request bodies can be passed around.
private struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}
