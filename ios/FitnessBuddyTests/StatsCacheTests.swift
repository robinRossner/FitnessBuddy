@testable import FitnessCore
import Foundation
import Testing
@testable import FitnessBuddy

@Suite(.tags(.stats))
struct StatsCacheStoreTests {
    @Test func diskCacheRoundTripsRawBytesAndTime() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("stats-cache-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = DiskStatsCache(directory: dir)
        let body = Data(#"{"a":"line\nbreak","b":[1,2]}"#.utf8)
        let at = Date(timeIntervalSince1970: 1_790_000_000.25)
        #expect(cache.read(namespace: "n", key: "k") == nil)
        cache.write(StatsCacheEntry(data: body, fetchedAt: at), namespace: "n", key: "k")
        let hit = try #require(cache.read(namespace: "n", key: "k"))
        #expect(hit.data == body && hit.fetchedAt == at)
        #expect(cache.read(namespace: "other", key: "k") == nil)   // namespaces are separate
        // A second instance (next launch) sees it.
        #expect(DiskStatsCache(directory: dir).read(namespace: "n", key: "k")?.data == body)
        cache.removeAll()
        #expect(cache.read(namespace: "n", key: "k") == nil)
    }

    @Test func endpointKeysIncludeSortedQuery() {
        #expect(StatsEndpoints.weeklyVolume(weeks: 26).key == "/api/v1/stats/volume/weekly?weeks=26")
        #expect(StatsEndpoints.weeklyVolume(weeks: 8).key != StatsEndpoints.weeklyVolume(weeks: 26).key)
        let e = StatsEndpoint<LiftsStatsDTO>(path: "/p", query: [URLQueryItem(name: "b", value: "2"), URLQueryItem(name: "a", value: "1")])
        #expect(e.key == "/p?a=1&b=2")
    }
}

/// Counts requests so the tests can tell cache hits from fetches.
final class CountingTransport: @unchecked Sendable {
    private let lock = NSLock()
    private var _count = 0
    var body: Data
    var failure: Error?
    init(body: String) { self.body = Data(body.utf8) }
    var count: Int { lock.lock(); defer { lock.unlock() }; return _count }
    func call() throws -> Data {
        lock.lock(); _count += 1; let f = failure; let b = body; lock.unlock()
        if let f { throw f }
        return b
    }
}

@MainActor
@Suite(.tags(.stats))
struct StatsRepositoryTests {
    private func makeRepo(_ transport: CountingTransport, store: StatsCacheStore = MemoryStatsCache(),
                          settings: SettingsStore? = nil) -> StatsRepository {
        let settings = settings ?? SettingsStore(defaults: UserDefaults(suiteName: "stats-tests-\(UUID().uuidString)")!, loadToken: false)
        let repo = StatsRepository(settings: settings, store: store)
        repo.transportOverride = ClosureTransport { _, _, _, _ in try transport.call() }
        return repo
    }

    private let liftsJSON = #"{"generated_at":"2026-10-25T11:00:00Z","lifts":[{"variant_id":1,"name":"Squat"}]}"#

    @Test func fetchCachesRawJsonAndCachedReadsItBack() async throws {
        let t = CountingTransport(body: liftsJSON)
        let repo = makeRepo(t)
        #expect(repo.cached(StatsEndpoints.lifts) == nil)
        let fresh = try await repo.fetch(StatsEndpoints.lifts, now: date("2026-10-25T12:00:00.000Z"))
        #expect(fresh.value.lifts.count == 1)
        let hit = try #require(repo.cached(StatsEndpoints.lifts))
        #expect(hit.fetchedAt == date("2026-10-25T12:00:00.000Z") && hit.value.lifts[0].name == "Squat")
        #expect(repo.store.read(namespace: repo.namespace, key: StatsEndpoints.lifts.key)?.data == Data(liftsJSON.utf8))
    }

    @Test func undecodableResponseIsNotCached() async throws {
        let t = CountingTransport(body: #"{"lifts":[{"name":"no id"}]}"#)
        let repo = makeRepo(t)
        await #expect(throws: APIError.self) { try await repo.fetch(StatsEndpoints.lifts) }
        #expect(repo.cached(StatsEndpoints.lifts) == nil)
    }

    @Test func resourceFetchesOnceWhileFreshAndAgainWhenStaleForcedOrAfterSync() async throws {
        let t = CountingTransport(body: liftsJSON)
        let repo = makeRepo(t)
        let r = repo.resource(StatsEndpoints.lifts)
        await r.load(generation: 1)
        #expect(t.count == 1 && r.value?.lifts.count == 1 && r.fetchedAt != nil && r.failure == nil)
        await r.load(generation: 1)                       // fresh: served from memory/cache, no request
        #expect(t.count == 1)
        await r.load(generation: 1, force: true)           // pull-to-refresh
        #expect(t.count == 2)
        await r.load(generation: 2)                       // a sync finished meanwhile
        #expect(t.count == 3)
        await r.load(generation: 2, maxAge: 0)             // older than the allowed age
        #expect(t.count == 4)
        await r.load(generation: 3, followsSync: false)    // sparkline style: ignores syncs while fresh
        #expect(t.count == 4)
    }

    @Test func cachedDataShowsImmediatelyAndOfflineKeepsIt() async throws {
        let store = MemoryStatsCache()
        let t = CountingTransport(body: liftsJSON)
        let first = makeRepo(t, store: store)
        _ = try await first.fetch(StatsEndpoints.lifts, now: date("2026-10-24T09:00:00.000Z"))
        // "Next launch": new repository, same disk store, server unreachable.
        let offline = CountingTransport(body: "")
        offline.failure = APIError.transport(URLError(.notConnectedToInternet))
        let second = makeRepo(offline, store: store)
        let r = second.resource(StatsEndpoints.lifts)
        await r.load(generation: 0)
        #expect(r.value?.lifts.first?.name == "Squat")                         // stale cache still shown
        #expect(r.fetchedAt == date("2026-10-24T09:00:00.000Z"))
        #expect(r.isOffline && r.failure != nil && !r.isRefreshing)
        #expect(offline.count == 1)
        offline.failure = nil
        offline.body = Data(liftsJSON.utf8)
        await r.load(generation: 0, force: true)
        #expect(!r.isOffline && r.failure == nil)
        #expect(r.fetchedAt! > date("2026-10-24T09:00:00.000Z"))
    }

    @Test func serverErrorIsNotReportedAsOffline() async {
        let t = CountingTransport(body: "")
        t.failure = APIError.http(500, "boom")
        let repo = makeRepo(t)
        let r = repo.resource(StatsEndpoints.lifts)
        await r.load()
        #expect(r.failure != nil && !r.isOffline && r.value == nil)
    }

    @Test func differentBackendsDoNotShareCache() async throws {
        let store = MemoryStatsCache()
        let t = CountingTransport(body: liftsJSON)
        let settings = SettingsStore(defaults: UserDefaults(suiteName: "stats-tests-\(UUID().uuidString)")!, loadToken: false)
        settings.update(url: "http://10.0.0.4:8000", token: "token-a")
        let repo = makeRepo(t, store: store, settings: settings)
        _ = try await repo.fetch(StatsEndpoints.lifts)
        #expect(repo.cached(StatsEndpoints.lifts) != nil)
        settings.update(url: "http://10.0.0.5:8000", token: "other-token")
        #expect(repo.cached(StatsEndpoints.lifts) == nil)
    }
}
