import FitnessCore
import Foundation
import Observation

/// Proposals, config history and coach settings: cached raw on disk like the stats, shown at once, refreshed in the
/// background. Reads work offline from the cache; every write (approve, reject, ask, rollback, mode) needs the network
/// and is never queued.
@MainActor
@Observable
final class CoachRepository: RemoteRepository {
    private(set) var proposals: [CoachProposalDTO] = []
    private(set) var proposalsFetchedAt: Date?
    private(set) var history: [CoachVersionDTO] = []
    private(set) var historyFetchedAt: Date?
    private(set) var settings: CoachSettingsDTO?
    private(set) var settingsFetchedAt: Date?
    private(set) var details: [String: CoachProposalDTO] = [:]
    private(set) var isRefreshing = false
    private(set) var failure: String?
    /// The last refresh could not reach the server (what is on screen is the cached copy).
    private(set) var isOffline = false
    private(set) var didFinishFirstLoad = false

    @ObservationIgnored private var lastNamespace: String?
    @ObservationIgnored private var lastGeneration: Int?
    @ObservationIgnored private var inFlight: Task<Void, Never>?
    /// Called with (pending count, newest pending id) whenever the proposal list changed (feeds the Today banner).
    @ObservationIgnored var onPendingChanged: ((Int, String?) -> Void)?
    /// Called after an approve / rollback changed the config: re-plans (sync), so Today and Progress follow.
    @ObservationIgnored var afterPlanChange: (() async -> Void)?

    static let maxAge: TimeInterval = 60

    init(settings: SettingsStore, network: NetworkMonitor, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool = { false }) {
        super.init(settings: settings, network: network, store: store, isDemo: isDemo)
    }

    // MARK: State

    var pendingCount: Int { proposals.filter(\.isPending).count }
    var groups: CoachLogic.Groups { CoachLogic.group(proposals) }
    var activeVersion: CoachVersionDTO? { history.first { $0.active } ?? history.first }

    func detail(_ id: String) -> CoachProposalDTO? { details[id] ?? proposals.first { $0.id == id } }

    func proposal(forTriggerRefId id: String) -> CoachProposalDTO? { proposals.first { $0.id == id } }

    // MARK: Loading

    /// Cache first, then the network when forced, after a sync, or when older than a minute.
    func load(generation: Int = 0, force: Bool = false) async {
        let ns = namespace
        if lastNamespace != ns {
            resetState()
            lastNamespace = ns
        }
        if proposals.isEmpty && history.isEmpty && settings == nil { await readCache(ns) }
        let generationChanged = lastGeneration != nil && lastGeneration != generation
        lastGeneration = generation
        let fresh = StatsFreshness.isFresh(fetchedAt: proposalsFetchedAt, maxAge: Self.maxAge)
        guard force || generationChanged || !fresh, canFetch else {
            didFinishFirstLoad = true
            return
        }
        await refreshAll()
    }

    /// Fetches list, history and settings together; a failure keeps the cached copy.
    func refreshAll() async {
        if let inFlight { await inFlight.value; return }
        isRefreshing = true
        let task: Task<Void, Never> = Task { [weak self] in
            guard let self else { return }
            async let a: Void = self.refreshProposals()
            async let b: Void = self.refreshHistory()
            async let c: Void = self.refreshSettings()
            _ = await (a, b, c)
        }
        inFlight = task
        await task.value
        inFlight = nil
        isRefreshing = false
        didFinishFirstLoad = true
    }

    func refreshProposals() async {
        await refresh(key: "proposals", path: CoachEndpoints.proposals,
                      query: [URLQueryItem(name: "status", value: "all"), URLQueryItem(name: "limit", value: "100")],
                      as: CoachProposalListDTO.self) { list in
            proposals = list.items.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
            for p in list.items { merge(p) }
            proposalsFetchedAt = .now
            notifyPending()
        }
    }

    func refreshHistory() async {
        await refresh(key: "history", path: CoachEndpoints.history, as: CoachHistoryDTO.self) { h in
            history = h.versions; historyFetchedAt = .now
        }
    }

    func refreshSettings() async {
        await refresh(key: "settings", path: CoachEndpoints.settings, as: CoachSettingsDTO.self) { s in
            settings = s; settingsFetchedAt = .now
        }
    }

    /// GET, decode off the main actor (a response that does not decode is not cached), cache the raw body, then apply.
    private func refresh<T: Decodable & Sendable>(key: String, path: String, query: [URLQueryItem] = [], as type: T.Type,
                                                  apply: (T) -> Void) async {
        guard let transport = transport() else { return }
        let ns = namespace
        do {
            let data = try await transport.get(path: path, query: query)
            let value: T
            do { value = try await Self.decodeCoach(T.self, data) } catch { throw APIError.decoding(error) }
            guard ns == namespace else { return }  // the backend changed while we waited
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: key)
            apply(value)
            failure = nil; isOffline = false
        } catch {
            guard let f = RemoteRepository.failure(for: error) else { return }
            failure = f.message; isOffline = f.offline
        }
    }

    /// The cached list, history and settings, decoded together off the main actor.
    private func readCache(_ ns: String) async {
        let p = store.read(namespace: ns, key: "proposals"), h = store.read(namespace: ns, key: "history"),
            s = store.read(namespace: ns, key: "settings")
        async let list = Self.cachedValue(CoachProposalListDTO.self, p)
        async let hist = Self.cachedValue(CoachHistoryDTO.self, h)
        async let sets = Self.cachedValue(CoachSettingsDTO.self, s)
        let (l, hv, sv) = await (list, hist, sets)
        guard ns == namespace else { return }  // the backend changed while decoding
        if let l, let p {
            proposals = l.items.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
            proposalsFetchedAt = p.fetchedAt
        }
        if let hv, let h { history = hv.versions; historyFetchedAt = h.fetchedAt }
        if let sv, let s { settings = sv; settingsFetchedAt = s.fetchedAt }
    }

    /// Coach payloads use the raw-key decoder (see `CoachCoding`).
    private nonisolated static func decodeCoach<T: Decodable & Sendable>(_ type: T.Type, _ data: Data) async throws -> T {
        try await decode(T.self, from: data, decoder: { CoachCoding.makeDecoder() })
    }

    private nonisolated static func cachedValue<T: Decodable & Sendable>(_ type: T.Type, _ entry: StatsCacheEntry?) async -> T? {
        guard let entry else { return nil }
        return try? await decodeCoach(T.self, entry.data)
    }

    private func resetState() {
        proposals = []; history = []; settings = nil; details = [:]
        proposalsFetchedAt = nil; historyFetchedAt = nil; settingsFetchedAt = nil
        failure = nil; isOffline = false
    }

    /// Backend or data was reset: forget everything (the next `load` starts clean).
    func reset() {
        store.removeAll()
        resetState()
        lastNamespace = nil
        lastGeneration = nil
        notifyPending()
    }

    private func merge(_ p: CoachProposalDTO) {
        var p = p
        p.inputSummary = p.inputSummary ?? details[p.id]?.inputSummary
        details[p.id] = p
    }

    private func notifyPending() {
        let pending = proposals.filter(\.isPending)
        onPendingChanged?(pending.count, pending.first?.id)
    }

    // MARK: One proposal

    /// Reads the cached copy of a proposal (offline detail view).
    func loadCachedDetail(_ id: String) async {
        guard details[id] == nil || details[id]?.inputSummary == nil,
              let p = await Self.cachedValue(CoachProposalDTO.self, store.read(namespace: namespace, key: "proposal/" + id))
        else { return }
        if details[id] == nil { merge(p) }
    }

    /// `GET /coach/proposals/{id}`: updates the detail, the list entry and the cache.
    @discardableResult
    func fetchProposal(_ id: String) async throws -> CoachProposalDTO {
        guard let transport = transport() else { throw CoachError.notConfigured }
        let ns = namespace
        do {
            let data = try await transport.get(path: CoachEndpoints.proposal(id))
            let p = try await decodeProposal(data)
            if ns == namespace {
                store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: "proposal/" + id)
                absorb(p)
            }
            return p
        } catch { throw CoachError.map(error) }
    }

    /// Polls a queued / running proposal every 3 s for up to 2 minutes (returns when the view goes away, too).
    func poll(_ id: String) async -> CoachPoller.Outcome {
        let outcome = await CoachPoller.run(fetch: { try await self.fetchProposal(id) })
        if case .finished = outcome { await refreshProposals() }
        return outcome
    }

    private func decodeProposal(_ data: Data) async throws -> CoachProposalDTO {
        do { return try await Self.decodeCoach(CoachProposalDTO.self, data) }
        catch { throw APIError.decoding(error) }
    }

    /// Puts a fresh copy into the detail map and the list (so the list and the detail never disagree).
    private func absorb(_ p: CoachProposalDTO) {
        merge(p)
        if let i = proposals.firstIndex(where: { $0.id == p.id }) { proposals[i] = details[p.id] ?? p }
        else { proposals.insert(p, at: 0) }
        notifyPending()
    }

    // MARK: Writes (online only, never queued)

    private func write(_ method: String, _ path: String, body: Encodable?) async throws -> Data {
        guard canWrite else { throw appSettings.isConfigured ? CoachError.needsConnection : CoachError.notConfigured }
        guard let transport = transport() else { throw CoachError.notConfigured }
        let data = try body.map { try CoachCoding.makeEncoder().encode(AnyEncodable($0)) }
        do { return try await transport.data(method: method, path: path, query: [], body: data) }
        catch { throw CoachError.map(error) }
    }

    /// Approve the given changes; then re-plan (sync) and refresh, so Today and Progress follow the new config.
    func approve(_ id: String, changeIds: [String]) async throws -> CoachProposalDTO {
        let data = try await write("POST", CoachEndpoints.approve(id), body: CoachApproveRequest(changeIds: changeIds))
        let p = try await decodeProposal(data)
        absorb(p)
        await configChanged()
        return details[id] ?? p
    }

    func reject(_ id: String, note: String?) async throws -> CoachProposalDTO {
        let trimmed = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        let data = try await write("POST", CoachEndpoints.reject(id), body: CoachRejectRequest(note: trimmed?.isEmpty == false ? trimmed : nil))
        let p = try await decodeProposal(data)
        absorb(p)
        await refreshProposals()
        return details[id] ?? p
    }

    /// 202 + the queued proposal; throws `.rateLimited(message)` on 429.
    func ask(scope: CoachScope, refId: String?, question: String) async throws -> CoachProposalDTO {
        let data = try await write("POST", CoachEndpoints.ask,
                                   body: CoachAskRequest(scope: scope, refId: refId, question: question.trimmingCharacters(in: .whitespacesAndNewlines)))
        let p = try await decodeProposal(data)
        absorb(p)
        await refreshSettings()  // calls this week
        return p
    }

    func rollback(to version: String) async throws {
        _ = try await write("POST", CoachEndpoints.rollback(version), body: nil)
        await configChanged()
    }

    func setApprovalMode(_ mode: ApprovalMode) async throws {
        let data = try await write("PUT", CoachEndpoints.settings, body: CoachSettingsUpdate(approvalMode: mode))
        if let s = try? await Self.decodeCoach(CoachSettingsDTO.self, data) {
            settings = s; settingsFetchedAt = .now
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: namespace, key: "settings")
        } else {
            await refreshSettings()
        }
    }

    private func configChanged() async {
        await afterPlanChange?()
        await refreshAll()
    }
}
