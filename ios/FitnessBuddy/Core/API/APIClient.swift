import Foundation

enum APIError: LocalizedError {
    case notConfigured
    case unauthorized
    case http(Int, String?)
    case transport(Error)
    case decoding(Error)

    /// Human-readable, shown in Settings and the "Test connection" result.
    var errorDescription: String? {
        switch self {
        case .notConfigured: "Backend URL or token not set"
        case .unauthorized: "Unauthorized: check the token"
        case .http(404, _): "Not found (404): check the backend URL"
        case .http(let code, let body): "Server error \(code)" + (body.flatMap { Self.snippet($0) }.map { ": \($0)" } ?? "")
        case .transport(let e): Self.describe(transport: e)
        case .decoding(let e): "Unexpected response (decoding): \(Self.describe(decoding: e))"
        }
    }

    /// Message for any error thrown while syncing / testing.
    static func message(for error: Error) -> String {
        if let api = error as? APIError { return api.errorDescription ?? "Unknown error" }
        return describe(transport: error)
    }

    static func describe(transport error: Error) -> String {
        guard let url = error as? URLError else { return error.localizedDescription }
        switch url.code {
        case .notConnectedToInternet, .networkConnectionLost, .cannotFindHost, .cannotConnectToHost, .timedOut,
             .dnsLookupFailed, .internationalRoamingOff, .dataNotAllowed:
            return "Can't reach server"
        case .appTransportSecurityRequiresSecureConnection:
            return "Insecure http:// connection blocked: use https://"
        case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
             .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid, .clientCertificateRejected:
            return "Secure connection failed (certificate or TLS)"
        case .cancelled: return "Cancelled"
        default: return "Network error: \(url.localizedDescription)"
        }
    }

    /// Names the offending field: `sessions[3].updated_at: Unparseable date`.
    static func describe(decoding error: Error) -> String {
        func path(_ ctx: DecodingError.Context, adding key: CodingKey? = nil) -> String {
            var parts = ctx.codingPath
            if let key { parts.append(key) }
            var out = ""
            for k in parts {
                if let i = k.intValue { out += "[\(i)]" } else { out += (out.isEmpty ? "" : ".") + k.stringValue }
            }
            return out.isEmpty ? "(root)" : out
        }
        switch error {
        case let DecodingError.keyNotFound(key, ctx): return "\(path(ctx, adding: key)) missing"
        case let DecodingError.valueNotFound(_, ctx): return "\(path(ctx)) is null"
        case let DecodingError.typeMismatch(_, ctx): return "\(path(ctx)) has the wrong type"
        case let DecodingError.dataCorrupted(ctx): return "\(path(ctx)): \(ctx.debugDescription)"
        default: return error.localizedDescription
        }
    }

    private static func snippet(_ body: String) -> String? {
        let t = body.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : String(t.prefix(120))
    }

    var isCancellation: Bool {
        if case .transport(let e) = self { return (e as? URLError)?.code == .cancelled || e is CancellationError }
        return false
    }

    /// True for failures a later retry could fix (network, 5xx); false for config / auth / contract errors.
    var isTransient: Bool {
        switch self {
        case .transport: true
        case .http(let code, _): code >= 500 || code == 408 || code == 429
        default: false
        }
    }
}

/// Thin async/await client. All contract details (paths, query names) live in `Endpoints` below so small
/// backend deviations are a one-line fix.
struct APIClient: Sendable {
    let baseURL: URL
    let token: String?
    var session: URLSession = APIClient.defaultSession

    static let defaultSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 20
        cfg.timeoutIntervalForResource = 60
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg)
    }()

    /// Normalises user input ("192.168.1.5:8000", trailing slashes, pasted `/api/v1`).
    static func normalizedBaseURL(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "http://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix("/api/v1") { s.removeLast("/api/v1".count) }
        guard let url = URL(string: s), url.host != nil else { return nil }
        return url
    }

    // MARK: Endpoints (the single place that knows the contract's paths)

    enum Endpoints {
        static let apiBase = "/api/v1"
        static let health = ["/api/v1/health", "/health"]  // tried in order
        static let planToday = apiBase + "/plan/today"
        static let planWeek = apiBase + "/plan/week"
        static let readiness = apiBase + "/readiness"
        static let syncPush = apiBase + "/sync/push"
        static let syncPull = apiBase + "/sync/pull"
        static func variantHistory(_ id: Int) -> String { apiBase + "/variants/\(id)/history" }
    }

    func health() async throws -> HealthDTO {
        var lastError: Error = APIError.notConfigured
        for path in Endpoints.health {
            do { return try await send(path: path, authorized: false) }
            catch APIError.http(404, _) { lastError = APIError.http(404, nil); continue }
        }
        throw lastError
    }

    func planToday() async throws -> PlanTodayDTO { try await send(path: Endpoints.planToday) }

    func planWeek(start: String? = nil) async throws -> PlanWeekDTO {
        try await send(path: Endpoints.planWeek, query: start.map { [URLQueryItem(name: "start", value: $0)] } ?? [])
    }

    func readiness(date: String? = nil) async throws -> ReadinessDTO {
        try await send(path: Endpoints.readiness, query: date.map { [URLQueryItem(name: "date", value: $0)] } ?? [])
    }

    func push(_ request: PushRequest) async throws -> PushResponse {
        try await send(path: Endpoints.syncPush, method: "POST", body: try APICoding.makeEncoder().encode(request))
    }

    func pull(since cursor: String?) async throws -> PullResponse {
        try await send(path: Endpoints.syncPull, query: [URLQueryItem(name: "since", value: cursor ?? "")])
    }

    func variantHistory(id: Int, limit: Int = 10) async throws -> [VariantHistoryEntryDTO] {
        try await send(path: Endpoints.variantHistory(id), query: [URLQueryItem(name: "limit", value: String(limit))])
    }

    // MARK: Transport

    func makeRequest(path: String, query: [URLQueryItem], method: String, authorized: Bool, body: Data?) throws -> URLRequest {
        guard var comps = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw APIError.notConfigured }
        comps.path = (comps.path.hasSuffix("/") ? String(comps.path.dropLast()) : comps.path) + path
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.notConfigured }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if authorized {
            guard let token, !token.isEmpty else { throw APIError.notConfigured }
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    /// Authorised GET returning the raw body (the stats cache stores raw JSON).
    func get(path: String, query: [URLQueryItem] = []) async throws -> Data {
        try await raw(path: path, query: query, method: "GET", authorized: true, body: nil)
    }

    private func raw(path: String, query: [URLQueryItem], method: String, authorized: Bool, body: Data?) async throws -> Data {
        let req = try makeRequest(path: path, query: query, method: method, authorized: authorized, body: body)
        let (respData, response): (Data, URLResponse)
        do { (respData, response) = try await session.data(for: req) }
        catch { throw APIError.transport(error) }
        guard let http = response as? HTTPURLResponse else { throw APIError.http(-1, nil) }
        switch http.statusCode {
        case 200..<300: return respData
        case 401, 403: throw APIError.unauthorized
        default: throw APIError.http(http.statusCode, String(data: respData, encoding: .utf8))
        }
    }

    private func send<T: Decodable>(path: String, query: [URLQueryItem] = [], method: String = "GET",
                                    authorized: Bool = true, body: Data? = nil) async throws -> T {
        let respData = try await raw(path: path, query: query, method: method, authorized: authorized, body: body)
        do { return try APICoding.makeDecoder().decode(T.self, from: respData) }
        catch { throw APIError.decoding(error) }
    }
}
