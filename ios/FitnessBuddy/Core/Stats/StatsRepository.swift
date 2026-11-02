import FitnessCore
import Foundation
import Observation

/// Fetches stats and keeps the raw JSON on disk. Views read the cache first and refresh in the background.
@MainActor
final class StatsRepository: RemoteRepository {
    private var resources: [String: AnyObject] = [:]

    override init(settings: SettingsStore, network: NetworkMonitor? = nil, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool = { false }) {
        super.init(settings: settings, network: network, store: store, isDemo: isDemo)
    }

    func cached<R: Decodable & Sendable>(_ endpoint: StatsEndpoint<R>) -> CachedStats<R>? {
        guard let entry = cacheEntry(endpoint.key),
              let value = try? APICoding.makeDecoder().decode(R.self, from: entry.data) else { return nil }
        return CachedStats(value: value, fetchedAt: entry.fetchedAt)
    }

    /// Network fetch; a response that does not decode is not cached.
    func fetch<R: Decodable & Sendable>(_ endpoint: StatsEndpoint<R>, now: Date = .now) async throws -> CachedStats<R> {
        guard let transport = transport() else { throw APIError.notConfigured }
        let ns = namespace
        let data = try await transport.get(path: endpoint.path, query: endpoint.query)
        let value: R
        do { value = try APICoding.makeDecoder().decode(R.self, from: data) }
        catch { throw APIError.decoding(error) }
        writeCache(data, key: endpoint.key, namespace: ns, at: now)
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
            guard let f = RemoteRepository.failure(for: error) else { return }
            failure = f.message; isOffline = f.offline
        }
    }
}
