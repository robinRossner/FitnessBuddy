import Foundation

/// A typed stats endpoint. The cache key is path + sorted query, so each parameter set is cached separately.
public struct StatsEndpoint<Response: Decodable & Sendable>: Sendable {
    public let path: String
    public var query: [URLQueryItem] = []

    public var key: String {
        path + "?" + query.sorted { $0.name < $1.name }.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
    }
}

/// Every stats endpoint the app reads. The only place that knows the paths.
public enum StatsEndpoints: Sendable {
    public static let lifts = StatsEndpoint<LiftsStatsDTO>(path: "/api/v1/stats/lifts")

    /// Full history of one lift variant; ranges (3M / 6M / 1Y / All) are cut on the device so one cache entry serves all.
    public static func liftDetail(_ variantId: Int) -> StatsEndpoint<LiftDetailDTO> {
        StatsEndpoint(path: "/api/v1/stats/lifts/\(variantId)")
    }
    public static func weeklyVolume(weeks: Int = 26) -> StatsEndpoint<WeeklyVolumeDTO> {
        StatsEndpoint(path: "/api/v1/stats/volume/weekly", query: [URLQueryItem(name: "weeks", value: String(weeks))])
    }
    public static func running(weeks: Int = 26) -> StatsEndpoint<RunningStatsDTO> {
        StatsEndpoint(path: "/api/v1/stats/running", query: [URLQueryItem(name: "weeks", value: String(weeks))])
    }
    public static func runAnalysis(_ runId: String) -> StatsEndpoint<RunAnalysisDTO> {
        StatsEndpoint(path: "/api/v1/runs/\(runId)/analysis")
    }
    public static func recentRuns(limit: Int = 30) -> StatsEndpoint<[RunSummaryDTO]> {
        StatsEndpoint(path: "/api/v1/runs", query: [URLQueryItem(name: "limit", value: String(limit))])
    }
    public static func health(days: Int = 90) -> StatsEndpoint<HealthStatsDTO> {
        StatsEndpoint(path: "/api/v1/stats/health", query: [URLQueryItem(name: "days", value: String(days))])
    }
    public static func correlations(days: Int = 120) -> StatsEndpoint<CorrelationsDTO> {
        StatsEndpoint(path: "/api/v1/stats/correlations", query: [URLQueryItem(name: "days", value: String(days))])
    }
}

public struct CachedStats<Response: Sendable>: Sendable {
    public var value: Response
    public var fetchedAt: Date

    public init(value: Response, fetchedAt: Date) { self.value = value; self.fetchedAt = fetchedAt }
}
