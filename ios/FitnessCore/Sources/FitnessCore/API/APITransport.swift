import Foundation

/// The one seam between the app and the backend: an authorised request that returns the raw body.
/// `APIClient` is the real one; the DEBUG demo and tests plug in their own (see `ClosureTransport`).
/// Errors follow `APIClient`: non-2xx answers throw `APIError.unauthorized` / `.http`, network failures `.transport`.
public protocol APITransport: Sendable {
    func data(method: String, path: String, query: [URLQueryItem], body: Data?) async throws -> Data
}

public extension APITransport {
    /// Authorised GET returning the raw body (the stats cache stores raw JSON).
    func get(path: String, query: [URLQueryItem] = []) async throws -> Data {
        try await data(method: "GET", path: path, query: query, body: nil)
    }

    /// Authorised request with an optional JSON body, returning the raw body (coach and gym writes).
    func perform(method: String, path: String, query: [URLQueryItem] = [], body: Data? = nil) async throws -> Data {
        try await data(method: method, path: path, query: query, body: body)
    }

    /// Request + decode with the app's decoder; a body that does not decode throws `APIError.decoding`.
    func send<T: Decodable>(_ type: T.Type = T.self, method: String = "GET", path: String, query: [URLQueryItem] = [],
                            body: Data? = nil) async throws -> T {
        let raw = try await data(method: method, path: path, query: query, body: body)
        do { return try APICoding.makeDecoder().decode(T.self, from: raw) }
        catch { throw APIError.decoding(error) }
    }

    // MARK: Typed endpoints (sync engine, rest times, templates, settings)

    func planToday() async throws -> PlanTodayDTO { try await send(path: APIClient.Endpoints.planToday) }

    func planWeek(start: String? = nil) async throws -> PlanWeekDTO {
        try await send(path: APIClient.Endpoints.planWeek, query: start.map { [URLQueryItem(name: "start", value: $0)] } ?? [])
    }

    func readiness(date: String? = nil) async throws -> ReadinessDTO {
        try await send(path: APIClient.Endpoints.readiness, query: date.map { [URLQueryItem(name: "date", value: $0)] } ?? [])
    }

    func push(_ request: PushRequest) async throws -> PushResponse {
        try await send(method: "POST", path: APIClient.Endpoints.syncPush, body: try APICoding.makeEncoder().encode(request))
    }

    func pull(since cursor: String?) async throws -> PullResponse {
        try await send(path: APIClient.Endpoints.syncPull, query: [URLQueryItem(name: "since", value: cursor ?? "")])
    }

    /// Sets (or, with nil, resets) the rest time of a variant's exercise. Idempotent on the server.
    func setRest(variantId: Int, restS: Int?) async throws -> RestResponseDTO {
        try await send(method: "PUT", path: APIClient.Endpoints.exerciseRest(variantId: variantId),
                       body: try APICoding.makeEncoder().encode(RestRequestDTO(restS: restS)))
    }

    /// Replaces the upper/lower template with the performed structure (server re-runs the engine).
    func updateProgram(kind: String, exercises: [TemplateExercise]) async throws {
        let body = try APICoding.makeEncoder().encode(TemplateUpdateRequest(exercises: exercises))
        _ = try await data(method: "PUT", path: APIClient.Endpoints.program(kind: kind), query: [], body: body)
    }

    func variantHistory(id: Int, limit: Int = 10) async throws -> [VariantHistoryEntryDTO] {
        try await send(path: APIClient.Endpoints.variantHistory(id), query: [URLQueryItem(name: "limit", value: String(limit))])
    }
}

/// A transport answered by a closure: the DEBUG demo data (synthetic JSON) and simple test fakes.
public struct ClosureTransport: APITransport {
    public typealias Handler = @Sendable (_ method: String, _ path: String, _ query: [URLQueryItem], _ body: Data?) async throws -> Data

    private let handler: Handler

    public init(_ handler: @escaping Handler) { self.handler = handler }

    public func data(method: String, path: String, query: [URLQueryItem], body: Data?) async throws -> Data {
        try await handler(method, path, query, body)
    }
}
