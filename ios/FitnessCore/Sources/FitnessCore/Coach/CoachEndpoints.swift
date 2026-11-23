import Foundation

/// Every coach endpoint the app uses. The only place that knows the paths.
public enum CoachEndpoints: Sendable {
    public static let proposals = "/api/v1/coach/proposals"
    public static let history = "/api/v1/coach/history"
    public static let settings = "/api/v1/coach/settings"
    public static let ask = "/api/v1/coach/ask"
    public static func proposal(_ id: String) -> String { proposals + "/" + id }
    public static func approve(_ id: String) -> String { proposal(id) + "/approve" }
    public static func reject(_ id: String) -> String { proposal(id) + "/reject" }
    public static func rollback(_ version: String) -> String { "/api/v1/config/rollback/" + version }
}

/// Failure of a coach action, in words for the screen.
public enum CoachError: LocalizedError, Equatable, Sendable {
    case needsConnection
    case notConfigured
    case rateLimited(String)
    case failed(String)

    public var errorDescription: String? {
        switch self {
        case .needsConnection: "Needs a connection"
        case .notConfigured: "Connect your backend in Settings first"
        case .rateLimited(let m), .failed(let m): m
        }
    }

    public static func map(_ error: Error) -> CoachError {
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

    /// The variant a 409 names: `{"detail": "...", "existing_variant_id": 7, "existing_variant_name": "..."}`.
    public static func existingVariant(_ body: String?) -> (id: Int, name: String)? {
        guard let body, let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = obj["existing_variant_id"] as? Int, let name = obj["existing_variant_name"] as? String else { return nil }
        return (id, name)
    }

    /// FastAPI errors look like `{"detail": "..."}` (or a list of validation errors).
    public static func detail(_ body: String?) -> String? {
        guard let body, let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        for key in ["detail", "message", "error"] {
            if let s = obj[key] as? String, !s.isEmpty { return s }
            if let list = obj[key] as? [[String: Any]], let msg = list.first?["msg"] as? String { return msg }
        }
        return nil
    }
}
