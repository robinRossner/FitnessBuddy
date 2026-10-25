import Foundation
import Observation

/// A typed stats endpoint. The cache key is path + sorted query, so each parameter set is cached separately.
struct StatsEndpoint<Response: Decodable & Sendable>: Sendable {
    let path: String
    var query: [URLQueryItem] = []

    var key: String {
        path + "?" + query.sorted { $0.name < $1.name }.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
    }
}

/// Every stats endpoint the app reads. The only place that knows the paths.
enum StatsEndpoints {
    static let lifts = StatsEndpoint<LiftsStatsDTO>(path: "/api/v1/stats/lifts")

    /// Full history of one lift variant; ranges (3M / 6M / 1Y / All) are cut on the device so one cache entry serves all.
    static func liftDetail(_ variantId: Int) -> StatsEndpoint<LiftDetailDTO> {
        StatsEndpoint(path: "/api/v1/stats/lifts/\(variantId)")
    }
    static func weeklyVolume(weeks: Int = 26) -> StatsEndpoint<WeeklyVolumeDTO> {
        StatsEndpoint(path: "/api/v1/stats/volume/weekly", query: [URLQueryItem(name: "weeks", value: String(weeks))])
    }
    static func running(weeks: Int = 26) -> StatsEndpoint<RunningStatsDTO> {
        StatsEndpoint(path: "/api/v1/stats/running", query: [URLQueryItem(name: "weeks", value: String(weeks))])
    }
    static func runAnalysis(_ runId: String) -> StatsEndpoint<RunAnalysisDTO> {
        StatsEndpoint(path: "/api/v1/runs/\(runId)/analysis")
    }
    static func recentRuns(limit: Int = 30) -> StatsEndpoint<[RunSummaryDTO]> {
        StatsEndpoint(path: "/api/v1/runs", query: [URLQueryItem(name: "limit", value: String(limit))])
    }
    static func health(days: Int = 90) -> StatsEndpoint<HealthStatsDTO> {
        StatsEndpoint(path: "/api/v1/stats/health", query: [URLQueryItem(name: "days", value: String(days))])
    }
    static func correlations(days: Int = 120) -> StatsEndpoint<CorrelationsDTO> {
        StatsEndpoint(path: "/api/v1/stats/correlations", query: [URLQueryItem(name: "days", value: String(days))])
    }
}

struct CachedStats<Response: Sendable>: Sendable {
    var value: Response
    var fetchedAt: Date
}

/// Fetches stats and keeps the raw JSON on disk. Views read the cache first and refresh in the background.
@MainActor
final class StatsRepository {
    typealias Transport = @Sendable (_ path: String, _ query: [URLQueryItem]) async throws -> Data

    let store: any StatsCacheStore
    private let settings: SettingsStore
    private let isDemo: @MainActor () -> Bool
    #if DEBUG
    /// DEBUG demo mode answers from synthetic JSON (same decode + cache path as the real thing).
    var demoTransport: Transport?
    #endif
    /// Tests inject a fake transport here.
    var transportOverride: Transport?
    private var resources: [String: AnyObject] = [:]

    init(settings: SettingsStore, store: any StatsCacheStore, isDemo: @escaping @MainActor () -> Bool = { false }) {
        self.settings = settings
        self.store = store
        self.isDemo = isDemo
    }

    var namespace: String { isDemo() ? "demo" : (settings.fingerprint ?? "unconfigured") }

    var canFetch: Bool { transport() != nil }

    private func transport() -> Transport? {
        if let transportOverride { return transportOverride }
        #if DEBUG
        if isDemo(), let demoTransport { return demoTransport }
        #endif
        guard !isDemo(), settings.isConfigured, let client = settings.makeClient() else { return nil }
        return { path, query in try await client.get(path: path, query: query) }
    }

    func cached<R: Decodable & Sendable>(_ endpoint: StatsEndpoint<R>) -> CachedStats<R>? {
        guard let entry = store.read(namespace: namespace, key: endpoint.key),
              let value = try? APICoding.makeDecoder().decode(R.self, from: entry.data) else { return nil }
        return CachedStats(value: value, fetchedAt: entry.fetchedAt)
    }

    /// Network fetch; a response that does not decode is not cached.
    func fetch<R: Decodable & Sendable>(_ endpoint: StatsEndpoint<R>, now: Date = .now) async throws -> CachedStats<R> {
        guard let transport = transport() else { throw APIError.notConfigured }
        let ns = namespace
        let data = try await transport(endpoint.path, endpoint.query)
        let value: R
        do { value = try APICoding.makeDecoder().decode(R.self, from: data) }
        catch { throw APIError.decoding(error) }
        store.write(StatsCacheEntry(data: data, fetchedAt: now), namespace: ns, key: endpoint.key)
        return CachedStats(value: value, fetchedAt: now)
    }

    /// One shared observable per endpoint, so every screen showing the same data agrees.
    func resource<R: Decodable & Sendable>(_ endpoint: StatsEndpoint<R>) -> StatsResource<R> {
        if let existing = resources[endpoint.key] as? StatsResource<R> { return existing }
        let created = StatsResource(endpoint: endpoint, repository: self)
        resources[endpoint.key] = created
        return created
    }

    func reset() {
        store.removeAll()
        resources.removeAll()
    }
}

@MainActor
protocol StatsLoadable: AnyObject {
    func load(generation: Int, maxAge: TimeInterval, force: Bool, followsSync: Bool) async
}

/// Observable state of one endpoint: cached value shown at once, refreshed in the background, never blocking.
@MainActor
@Observable
final class StatsResource<R: Decodable & Sendable>: StatsLoadable {
    private(set) var value: R?
    private(set) var fetchedAt: Date?
    private(set) var isRefreshing = false
    private(set) var failure: String?
    /// The last refresh failed because the server could not be reached (so the data on screen is the cached copy).
    private(set) var isOffline = false
    private(set) var didFinishFirstLoad = false

    let endpoint: StatsEndpoint<R>
    @ObservationIgnored private weak var repository: StatsRepository?
    @ObservationIgnored private var lastGeneration: Int?
    @ObservationIgnored private var lastNamespace: String?
    @ObservationIgnored private var inFlight: Task<Void, Never>?

    init(endpoint: StatsEndpoint<R>, repository: StatsRepository) {
        self.endpoint = endpoint
        self.repository = repository
    }

    var isConfigured: Bool { repository?.canFetch ?? false }

    /// Reads the cache (synchronously), then refetches when forced, when a sync happened since last time, or when
    /// the cached copy is older than `maxAge` (`followsSync: false` ignores the sync, for cheap list decorations such as
    /// sparklines). Concurrent callers join the request already running.
    func load(generation: Int = 0, maxAge: TimeInterval = StatsFreshness.maxAge, force: Bool = false,
              followsSync: Bool = true) async {
        guard let repository else { return }
        let ns = repository.namespace
        let generationChanged = followsSync && lastGeneration != nil && lastGeneration != generation
        if value == nil || lastNamespace != ns || generationChanged {
            if let hit = repository.cached(endpoint) {
                value = hit.value; fetchedAt = hit.fetchedAt
            } else if lastNamespace != ns {
                value = nil; fetchedAt = nil
            }
            lastNamespace = ns
        }
        lastGeneration = generation
        let needsFetch = force || generationChanged || !StatsFreshness.isFresh(fetchedAt: fetchedAt, maxAge: maxAge)
        guard needsFetch, repository.canFetch else {
            didFinishFirstLoad = true
            return
        }
        if let inFlight { await inFlight.value; return }
        isRefreshing = true
        let task: Task<Void, Never> = Task { [weak self] in if let self { await self.refresh() } }
        inFlight = task
        await task.value
    }

    private func refresh() async {
        defer { inFlight = nil; isRefreshing = false; didFinishFirstLoad = true }
        guard let repository else { return }
        do {
            let fresh = try await repository.fetch(endpoint)
            value = fresh.value; fetchedAt = fresh.fetchedAt
            failure = nil; isOffline = false
        } catch {
            if Task.isCancelled || (error as? APIError)?.isCancellation == true || error is CancellationError { return }
            failure = APIError.message(for: error)
            if case APIError.transport = error { isOffline = true } else { isOffline = false }
        }
    }
}
