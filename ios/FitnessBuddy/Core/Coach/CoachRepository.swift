import Foundation
import Observation

/// Every coach endpoint the app uses. The only place that knows the paths.
enum CoachEndpoints {
    static let proposals = "/api/v1/coach/proposals"
    static let history = "/api/v1/coach/history"
    static let settings = "/api/v1/coach/settings"
    static let ask = "/api/v1/coach/ask"
    static func proposal(_ id: String) -> String { proposals + "/" + id }
    static func approve(_ id: String) -> String { proposal(id) + "/approve" }
    static func reject(_ id: String) -> String { proposal(id) + "/reject" }
    static func rollback(_ version: String) -> String { "/api/v1/config/rollback/" + version }
}

/// Failure of a coach action, in words for the screen.
enum CoachError: LocalizedError, Equatable {
    case needsConnection
    case notConfigured
    case rateLimited(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .needsConnection: "Needs a connection"
        case .notConfigured: "Connect your backend in Settings first"
        case .rateLimited(let m), .failed(let m): m
        }
    }

    static func map(_ error: Error) -> CoachError {
        if let e = error as? CoachError { return e }
        if let api = error as? APIError {
            switch api {
            case .transport: return .needsConnection
            case .notConfigured: return .notConfigured
            case .http(429, let body): return .rateLimited(detail(body) ?? "The coach is rate limited right now. Try again later.")
            case .http(let code, let body): return .failed(detail(body) ?? "Server error \(code)")
            default: return .failed(api.errorDescription ?? "Something went wrong")
            }
        }
        return .failed(error.localizedDescription)
    }

    /// FastAPI errors look like `{"detail": "..."}` (or a list of validation errors).
    static func detail(_ body: String?) -> String? {
        guard let body, let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        for key in ["detail", "message", "error"] {
            if let s = obj[key] as? String, !s.isEmpty { return s }
            if let list = obj[key] as? [[String: Any]], let msg = list.first?["msg"] as? String { return msg }
        }
        return nil
    }
}

/// Proposals, config history and coach settings: cached raw on disk like the stats, shown at once, refreshed in the
/// background. Reads work offline from the cache; every write (approve, reject, ask, rollback, mode) needs the network
/// and is never queued.
@MainActor
@Observable
final class CoachRepository {
    typealias Transport = @Sendable (_ method: String, _ path: String, _ query: [URLQueryItem], _ body: Data?) async throws -> Data

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

    @ObservationIgnored private let store: any StatsCacheStore
    @ObservationIgnored private let appSettings: SettingsStore
    @ObservationIgnored private let network: NetworkMonitor
    @ObservationIgnored private let isDemo: @MainActor () -> Bool
    @ObservationIgnored private var lastNamespace: String?
    @ObservationIgnored private var lastGeneration: Int?
    @ObservationIgnored private var inFlight: Task<Void, Never>?
    #if DEBUG
    @ObservationIgnored var demoTransport: Transport?
    #endif
    /// Tests inject a fake transport here.
    @ObservationIgnored var transportOverride: Transport?
    /// Called with (pending count, newest pending id) whenever the proposal list changed (feeds the Today banner).
    @ObservationIgnored var onPendingChanged: ((Int, String?) -> Void)?
    /// Called after an approve / rollback changed the config: re-plans (sync), so Today and Progress follow.
    @ObservationIgnored var afterPlanChange: (() async -> Void)?

    static let maxAge: TimeInterval = 60

    init(settings: SettingsStore, network: NetworkMonitor, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool = { false }) {
        self.appSettings = settings
        self.network = network
        self.store = store
        self.isDemo = isDemo
    }

    // MARK: State

    var namespace: String { isDemo() ? "demo" : (appSettings.fingerprint ?? "unconfigured") }

    var pendingCount: Int { proposals.filter(\.isPending).count }
    var groups: CoachLogic.Groups { CoachLogic.group(proposals) }
    var activeVersion: CoachVersionDTO? { history.first { $0.active } ?? history.first }

    var canFetch: Bool { transport() != nil }

    /// Writes need a reachable server (the demo answers locally).
    var canWrite: Bool { isDemo() || (appSettings.isConfigured && network.isConnected) }

    var writeBlockedHint: String? {
        if isDemo() || canWrite { return nil }
        return appSettings.isConfigured ? "Needs a connection" : "Connect your backend in Settings"
    }

    func detail(_ id: String) -> CoachProposalDTO? { details[id] ?? proposals.first { $0.id == id } }

    func proposal(forTriggerRefId id: String) -> CoachProposalDTO? { proposals.first { $0.id == id } }

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

    /// Cache first, then the network when forced, after a sync, or when older than a minute.
    func load(generation: Int = 0, force: Bool = false) async {
        let ns = namespace
        if lastNamespace != ns {
            resetState()
            lastNamespace = ns
        }
        if proposals.isEmpty && history.isEmpty && settings == nil { readCache(ns) }
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
        await refresh(key: "proposals", method: "GET", path: CoachEndpoints.proposals,
                      query: [URLQueryItem(name: "status", value: "all"), URLQueryItem(name: "limit", value: "100")]) { data in
            let list = try CoachCoding.makeDecoder().decode(CoachProposalListDTO.self, from: data)
            return { [self] in
                proposals = list.items.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
                for p in list.items { merge(p) }
                proposalsFetchedAt = .now
                notifyPending()
            }
        }
    }

    func refreshHistory() async {
        await refresh(key: "history", method: "GET", path: CoachEndpoints.history, query: []) { data in
            let h = try CoachCoding.makeDecoder().decode(CoachHistoryDTO.self, from: data)
            return { [self] in history = h.versions; historyFetchedAt = .now }
        }
    }

    func refreshSettings() async {
        await refresh(key: "settings", method: "GET", path: CoachEndpoints.settings, query: []) { data in
            let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: data)
            return { [self] in settings = s; settingsFetchedAt = .now }
        }
    }

    /// GET, decode (a response that does not decode is not cached), cache the raw body, then apply.
    private func refresh(key: String, method: String, path: String, query: [URLQueryItem],
                         decode: (Data) throws -> (@MainActor () -> Void)) async {
        guard let transport = transport() else { return }
        let ns = namespace
        do {
            let data = try await transport(method, path, query, nil)
            let apply: @MainActor () -> Void
            do { apply = try decode(data) } catch { throw APIError.decoding(error) }
            guard ns == namespace else { return }  // the backend changed while we waited
            store.write(StatsCacheEntry(data: data, fetchedAt: .now), namespace: ns, key: key)
            apply()
            failure = nil; isOffline = false
        } catch {
            if Task.isCancelled || (error as? APIError)?.isCancellation == true || error is CancellationError { return }
            failure = APIError.message(for: error)
            if case APIError.transport = error { isOffline = true } else { isOffline = false }
        }
    }

    private func readCache(_ ns: String) {
        let decoder = CoachCoding.makeDecoder()
        if let e = store.read(namespace: ns, key: "proposals"), let list = try? decoder.decode(CoachProposalListDTO.self, from: e.data) {
            proposals = list.items.sorted { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }
            proposalsFetchedAt = e.fetchedAt
        }
        if let e = store.read(namespace: ns, key: "history"), let h = try? decoder.decode(CoachHistoryDTO.self, from: e.data) {
            history = h.versions; historyFetchedAt = e.fetchedAt
        }
        if let e = store.read(namespace: ns, key: "settings"), let s = try? decoder.decode(CoachSettingsDTO.self, from: e.data) {
            settings = s; settingsFetchedAt = e.fetchedAt
        }
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
    func loadCachedDetail(_ id: String) {
        guard details[id] == nil || details[id]?.inputSummary == nil,
              let e = store.read(namespace: namespace, key: "proposal/" + id),
              let p = try? CoachCoding.makeDecoder().decode(CoachProposalDTO.self, from: e.data) else { return }
        if details[id] == nil { merge(p) }
    }

    /// `GET /coach/proposals/{id}`: updates the detail, the list entry and the cache.
    @discardableResult
    func fetchProposal(_ id: String) async throws -> CoachProposalDTO {
        guard let transport = transport() else { throw CoachError.notConfigured }
        let ns = namespace
        do {
            let data = try await transport("GET", CoachEndpoints.proposal(id), [], nil)
            let p = try decodeProposal(data)
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

    private func decodeProposal(_ data: Data) throws -> CoachProposalDTO {
        do { return try CoachCoding.makeDecoder().decode(CoachProposalDTO.self, from: data) }
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
        do { return try await transport(method, path, [], data) }
        catch { throw CoachError.map(error) }
    }

    /// Approve the given changes; then re-plan (sync) and refresh, so Today and Progress follow the new config.
    func approve(_ id: String, changeIds: [String]) async throws -> CoachProposalDTO {
        let data = try await write("POST", CoachEndpoints.approve(id), body: CoachApproveRequest(changeIds: changeIds))
        let p = try decodeProposal(data)
        absorb(p)
        await configChanged()
        return details[id] ?? p
    }

    func reject(_ id: String, note: String?) async throws -> CoachProposalDTO {
        let trimmed = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        let data = try await write("POST", CoachEndpoints.reject(id), body: CoachRejectRequest(note: trimmed?.isEmpty == false ? trimmed : nil))
        let p = try decodeProposal(data)
        absorb(p)
        await refreshProposals()
        return details[id] ?? p
    }

    /// 202 + the queued proposal; throws `.rateLimited(message)` on 429.
    func ask(scope: CoachScope, refId: String?, question: String) async throws -> CoachProposalDTO {
        let data = try await write("POST", CoachEndpoints.ask,
                                   body: CoachAskRequest(scope: scope, refId: refId, question: question.trimmingCharacters(in: .whitespacesAndNewlines)))
        let p = try decodeProposal(data)
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
        if let s = try? CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: data) {
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

/// Type-erases an `Encodable` so request bodies can be passed around.
private struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}
