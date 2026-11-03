import FitnessCore
import Foundation

/// What Stats, Coach and Gyms share: the cache namespace (backend fingerprint or "demo"), which transport answers
/// (test override, DEBUG demo, or the configured backend), whether writes may go out, and the raw-JSON disk cache.
/// Subclasses keep their own observable state; this base holds no observable properties.
@MainActor
class RemoteRepository {
    let store: any StatsCacheStore
    let appSettings: SettingsStore
    /// Nil for read-only repositories (stats): writes are then only gated on the configuration.
    private let network: NetworkMonitor?
    private let isDemoProvider: @MainActor () -> Bool
    #if DEBUG
    /// DEBUG demo mode answers from synthetic JSON (same decode + cache path as the real thing).
    var demoTransport: (any APITransport)?
    #endif
    /// Tests inject a fake transport here.
    var transportOverride: (any APITransport)?

    init(settings: SettingsStore, network: NetworkMonitor?, store: any StatsCacheStore,
         isDemo: @escaping @MainActor () -> Bool) {
        self.appSettings = settings
        self.network = network
        self.store = store
        self.isDemoProvider = isDemo
    }

    var isDemo: Bool { isDemoProvider() }

    /// Cache namespace: the backend fingerprint, so switching backends never shows another backend's data.
    var namespace: String { isDemo ? "demo" : (appSettings.fingerprint ?? "unconfigured") }

    var canFetch: Bool { transport() != nil }

    /// Writes need a reachable server (the demo answers locally).
    var canWrite: Bool { isDemo || (appSettings.isConfigured && (network?.isConnected ?? true)) }

    var writeBlockedHint: String? {
        if canWrite { return nil }
        return appSettings.isConfigured ? "Needs a connection" : "Connect your backend in Settings"
    }

    func transport() -> (any APITransport)? {
        if let transportOverride { return transportOverride }
        #if DEBUG
        if isDemo, let demoTransport { return demoTransport }
        #endif
        guard !isDemo, appSettings.isConfigured else { return nil }
        return appSettings.makeClient()
    }

    // MARK: Cache

    func cacheEntry(_ key: String, namespace ns: String? = nil) -> StatsCacheEntry? {
        store.read(namespace: ns ?? namespace, key: key)
    }

    func writeCache(_ data: Data, key: String, namespace ns: String, at date: Date = AppClock.instant) {
        store.write(StatsCacheEntry(data: data, fetchedAt: date), namespace: ns, key: key)
    }

    // MARK: Decoding

    /// Decodes off the main actor (a large stats or coach payload would otherwise stall scrolling and navigation);
    /// the caller gets the value back on its own actor.
    nonisolated static func decode<T: Decodable & Sendable>(
        _ type: T.Type, from data: Data, decoder: @escaping @Sendable () -> JSONDecoder = { APICoding.makeDecoder() }
    ) async throws -> T {
        try await Task.detached(priority: .userInitiated) { try decoder().decode(T.self, from: data) }.value
    }

    // MARK: Errors

    /// What a failed refresh shows: the message, and whether the server was unreachable (the screen then shows the
    /// cached copy as offline). Nil for a cancellation, which shows nothing.
    static func failure(for error: Error) -> (message: String, offline: Bool)? {
        if Task.isCancelled || (error as? APIError)?.isCancellation == true || error is CancellationError { return nil }
        if case APIError.transport = error { return (APIError.message(for: error), true) }
        return (APIError.message(for: error), false)
    }
}

/// Type-erases an `Encodable` so request bodies can be passed around.
struct AnyEncodable: Encodable {
    let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}
