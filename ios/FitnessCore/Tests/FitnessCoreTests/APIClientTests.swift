import Foundation
import Testing
@testable import FitnessCore

/// URL loading stub for `APIClient`: records the requests it sees and answers with the handler's status + body.
final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, String))?
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        do {
            let (status, json) = try Self.handler?(request) ?? (500, "{}")
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(json.utf8))
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    static func client(token: String? = "secret") -> APIClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [StubURLProtocol.self]
        requests = []
        return APIClient(baseURL: URL(string: "http://stub.test")!, token: token, session: URLSession(configuration: cfg))
    }
}

/// The real client's request building and status mapping (everything else talks to a fake `APITransport`).
@Suite(.serialized, .tags(.sync, .fast))
struct APIClientTests {
    @Test func authorisedRequestsCarryTheBearerToken() async throws {
        let today = String(decoding: try Fixture.data("plan_today"), as: UTF8.self)
        StubURLProtocol.handler = { _ in (200, today) }
        _ = try await StubURLProtocol.client().planToday()
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer secret")
        #expect(request.value(forHTTPHeaderField: "Accept") == "application/json")
        #expect(request.url?.path == "/api/v1/plan/today")
    }

    @Test func aMissingTokenFailsBeforeAnyRequest() async {
        StubURLProtocol.handler = { _ in (200, "{}") }
        await #expect(throws: APIError.self) { _ = try await StubURLProtocol.client(token: nil).planToday() }
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func unauthorisedMapsToTheAuthError() async {
        StubURLProtocol.handler = { _ in (401, #"{"detail": "bad token"}"#) }
        do {
            _ = try await StubURLProtocol.client().get(path: "/api/v1/stats/lifts")
            Issue.record("expected an error")
        } catch let error as APIError {
            guard case .unauthorized = error else { Issue.record("got \(error)"); return }
            #expect(!error.isTransient)
        } catch { Issue.record("got \(error)") }
    }

    @Test func serverErrorsKeepStatusAndBodyAndAreTransient() async {
        StubURLProtocol.handler = { _ in (503, "maintenance") }
        do {
            _ = try await StubURLProtocol.client().get(path: "/api/v1/stats/lifts")
            Issue.record("expected an error")
        } catch let error as APIError {
            guard case .http(503, let body) = error else { Issue.record("got \(error)"); return }
            #expect(body == "maintenance")
            #expect(error.isTransient)
        } catch { Issue.record("got \(error)") }
    }

    @Test func networkFailuresBecomeTransportErrors() async {
        StubURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        do {
            _ = try await StubURLProtocol.client().get(path: "/api/v1/stats/lifts")
            Issue.record("expected an error")
        } catch let error as APIError {
            guard case .transport = error else { Issue.record("got \(error)"); return }
            #expect(APIError.message(for: error) == "Can't reach server")
        } catch { Issue.record("got \(error)") }
    }

    @Test func validateMapsStatuses() throws {
        #expect(try APIClient.validate(status: 204, body: Data("x".utf8)) == Data("x".utf8))
        #expect(throws: APIError.self) { try APIClient.validate(status: 403, body: Data()) }
        #expect(throws: APIError.self) { try APIClient.validate(status: 422, body: Data()) }
    }
}
