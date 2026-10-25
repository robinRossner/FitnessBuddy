import CryptoKit
import Foundation

/// One cached response: the raw JSON body as the backend sent it, plus when it was fetched.
struct StatsCacheEntry: Sendable, Equatable {
    var data: Data
    var fetchedAt: Date
}

/// Key-value store for stats responses, keyed by (namespace, endpoint key). The namespace is the backend fingerprint
/// (or "demo"), so switching backends never shows another backend's numbers.
protocol StatsCacheStore: Sendable {
    func read(namespace: String, key: String) -> StatsCacheEntry?
    func write(_ entry: StatsCacheEntry, namespace: String, key: String)
    func removeAll()
}

final class MemoryStatsCache: StatsCacheStore, @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [String: StatsCacheEntry] = [:]

    func read(namespace: String, key: String) -> StatsCacheEntry? {
        lock.lock(); defer { lock.unlock() }
        return entries[namespace + "|" + key]
    }
    func write(_ entry: StatsCacheEntry, namespace: String, key: String) {
        lock.lock(); defer { lock.unlock() }
        entries[namespace + "|" + key] = entry
    }
    func removeAll() {
        lock.lock(); defer { lock.unlock() }
        entries.removeAll()
    }
}

/// One file per entry under Application Support/StatsCache: first line is the fetch time (unix seconds), the rest is
/// the raw JSON. Survives launches and works offline.
final class DiskStatsCache: StatsCacheStore, @unchecked Sendable {
    private let directory: URL

    init(directory: URL? = nil) {
        let base = directory ?? (FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory).appendingPathComponent("StatsCache", isDirectory: true)
        self.directory = base
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
    }

    private func url(_ namespace: String, _ key: String) -> URL {
        let digest = SHA256.hash(data: Data((namespace + "|" + key).utf8))
        let name = digest.prefix(16).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent(name + ".json")
    }

    func read(namespace: String, key: String) -> StatsCacheEntry? {
        guard let blob = try? Data(contentsOf: url(namespace, key)),
              let newline = blob.firstIndex(of: 0x0A),
              let head = String(data: blob[blob.startIndex..<newline], encoding: .utf8),
              let seconds = TimeInterval(head) else { return nil }
        return StatsCacheEntry(data: blob[blob.index(after: newline)...], fetchedAt: Date(timeIntervalSince1970: seconds))
    }

    func write(_ entry: StatsCacheEntry, namespace: String, key: String) {
        var blob = Data("\(entry.fetchedAt.timeIntervalSince1970)\n".utf8)
        blob.append(entry.data)
        try? blob.write(to: url(namespace, key), options: .atomic)
    }

    func removeAll() {
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
}

/// When cached data is old enough to refetch, and how the "Updated ..." line reads.
enum StatsFreshness {
    /// Refetch on appear once the cache is older than this.
    static let maxAge: TimeInterval = 15 * 60

    static func isFresh(fetchedAt: Date?, now: Date = .now, maxAge: TimeInterval = StatsFreshness.maxAge) -> Bool {
        guard let fetchedAt else { return false }
        let age = now.timeIntervalSince(fetchedAt)
        return age >= 0 && age < maxAge  // a timestamp from the future (clock change) counts as stale
    }

    /// "Updated just now" / "Updated 2 h ago" / "Offline, showing data from Oct 24, 14:03".
    static func label(fetchedAt: Date?, offline: Bool, now: Date = .now, calendar: Calendar = .current,
                      locale: Locale = Locale(identifier: "en_US_POSIX")) -> String? {
        guard let fetchedAt else { return offline ? "Offline" : nil }
        if offline {
            let f = DateFormatter()
            f.calendar = calendar; f.timeZone = calendar.timeZone; f.locale = locale
            f.dateFormat = calendar.isDate(fetchedAt, inSameDayAs: now) ? "HH:mm" : "MMM d, HH:mm"
            return "Offline, showing data from \(f.string(from: fetchedAt))"
        }
        return "Updated " + relative(fetchedAt, now: now)
    }

    static func relative(_ date: Date, now: Date = .now) -> String {
        let s = max(0, now.timeIntervalSince(date))
        switch s {
        case ..<60: return "just now"
        case ..<3600: return "\(Int(s / 60)) min ago"
        case ..<86_400: return "\(Int(s / 3600)) h ago"
        default: return "\(Int(s / 86_400)) d ago"
        }
    }
}
