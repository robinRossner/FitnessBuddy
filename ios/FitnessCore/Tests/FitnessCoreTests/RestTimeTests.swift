import Foundation
import Testing
@testable import FitnessCore

// MARK: - Formatting, picker values, resolution order

@Suite(.tags(.workout, .fast))
struct RestTimeBasicsTests {
    @Test func formatsMinutesAndSeconds() {
        #expect(RestTime.format(120) == "2:00")
        #expect(RestTime.format(135) == "2:15")
        #expect(RestTime.format(60) == "1:00")
        #expect(RestTime.format(300) == "5:00")
        #expect(RestTime.defaultLabel(default: 120) == "Default (2:00)")
    }

    @Test func pickerOffersOneToFiveMinutesInFifteenSecondSteps() {
        let values = RestTime.pickerValues
        #expect(values.first == 60 && values.last == 300)
        #expect(values.count == 17)
        #expect(zip(values, values.dropFirst()).allSatisfy { $1 - $0 == 15 })
        #expect(values.contains(120) && values.contains(180) && values.contains(135))
    }

    @Test func aCurrentValueOutsideTheListStaysSelectable() {
        #expect(RestTime.pickerChoices(current: nil) == RestTime.pickerValues)
        #expect(RestTime.pickerChoices(current: 180) == RestTime.pickerValues)
        let withCoachValue = RestTime.pickerChoices(current: 45)
        #expect(withCoachValue.first == 45 && withCoachValue.count == 18 && withCoachValue == withCoachValue.sorted())
        #expect(RestTime.pickerChoices(current: 400).last == 400)
    }

    @Test func fallbackIsTwoMinutes() {
        #expect(RestTime.fallbackSeconds == 120)
    }
}

// MARK: - Decoding

@Suite(.tags(.workout, .decoding, .fast))
struct RestDecodingTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    @Test func planExerciseWithRestS() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"variant_id": 5, "name": "Squat", "rest_s": 180, "sets": []}"#)
        #expect(ex.restS == 180)
    }

    @Test func planExerciseWithoutRestS() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"variant_id": 5, "name": "Squat", "sets": []}"#)
        #expect(ex.restS == nil)
        // the current server sends rest_s; the same plan without it (an older server) decodes to nil
        let current = try Fixture.decode(PlanTodayDTO.self, "plan_today")
        #expect(current.sessions.first?.session?.exercises?.allSatisfy { $0.restS != nil } == true)
        func dropRest(_ v: Any) -> Any {
            if let d = v as? [String: Any] { return d.filter { $0.key != "rest_s" }.mapValues(dropRest) }
            if let a = v as? [Any] { return a.map(dropRest) }
            return v
        }
        let stripped = try JSONSerialization.data(withJSONObject: dropRest(JSONSerialization.jsonObject(with: Fixture.data("plan_today"))))
        #expect(!String(decoding: stripped, as: UTF8.self).contains("rest_s"))
        let existing = try APICoding.makeDecoder().decode(PlanTodayDTO.self, from: stripped)
        #expect(existing.sessions.first?.session?.exercises?.allSatisfy { $0.restS == nil } == true)
    }

    @Test func planExerciseRestSSurvivesTheCacheRoundTrip() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"name": "Row", "rest_s": 150, "sets": []}"#)
        let again = try APICoding.makeDecoder().decode(PlannedExerciseDTO.self, from: APICoding.makeEncoder().encode(ex))
        #expect(again.restS == 150)
    }

    @Test func configWithAndWithoutTheLiftingBlock() throws {
        let with = try decode(ConfigDTO.self, #"""
        {"version": "v5", "lifting": {"rest_default_s": 120, "rest_s_by_variant": {"122": 180, "7": 150}}}
        """#)
        #expect(with.lifting?.restDefaultS == 120)
        #expect(with.lifting?.restSByVariant == ["122": 180, "7": 150])
        let without = try decode(ConfigDTO.self, #"{"version": "v3", "timezone": "Europe/Vienna"}"#)
        #expect(without.lifting == nil)
        let again = try APICoding.makeDecoder().decode(ConfigDTO.self, from: APICoding.makeEncoder().encode(with))
        #expect(again == with)
    }

    @Test func requestAlwaysWritesRestS() throws {
        let set = String(decoding: try APICoding.makeEncoder().encode(RestRequestDTO(restS: 180)), as: UTF8.self)
        #expect(set == #"{"rest_s":180}"#)
        let reset = String(decoding: try APICoding.makeEncoder().encode(RestRequestDTO(restS: nil)), as: UTF8.self)
        #expect(reset == #"{"rest_s":null}"#)
    }

    @Test func response() throws {
        let r = try decode(RestResponseDTO.self, #"{"variant_id": 122, "rest_s": 180, "is_default": false, "config_version": "v3.4"}"#)
        #expect(r == RestResponseDTO(variantId: 122, restS: 180, isDefault: false, configVersion: "v3.4"))
        let old = try decode(RestResponseDTO.self, #"{"variant_id": 122, "rest_s": 120}"#)
        #expect(old.isDefault == nil)
    }
}

// MARK: - Upload (PUT /exercises/{id}/rest)

final class RestStubProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, String))?
    nonisolated(unsafe) static var requests: [(method: String, path: String, body: String)] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open()
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                data.append(buffer, count: n)
            }
            stream.close()
            body = data
        }
        Self.requests.append((request.httpMethod ?? "", request.url?.path ?? "", String(decoding: body ?? Data(), as: UTF8.self)))
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

    static func client() -> APIClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [RestStubProtocol.self]
        requests = []
        return APIClient(baseURL: URL(string: "http://stub.test")!, token: "t", session: URLSession(configuration: cfg))
    }
}
