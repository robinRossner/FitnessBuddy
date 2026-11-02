@testable import FitnessCore
import Foundation
import Testing
@testable import FitnessBuddy

@MainActor
@Suite(.tags(.progress, .fast))
struct MemoTests {
    @Test func recomputesOnlyWhenTheKeyChanges() {
        let memo = Memo<Int, String>()
        var runs = 0
        let compute = { (n: Int) -> String in runs += 1; return "v\(n)" }
        #expect(memo(1) { compute(1) } == "v1")
        #expect(memo(1) { compute(1) } == "v1")
        #expect(runs == 1)
        #expect(memo(2) { compute(2) } == "v2")
        #expect(runs == 2)
    }

    @Test func planItemsDecodeOncePerPayload() throws {
        let payload = Data(#"{"date": "2026-10-26", "kind": "upper", "title": "Upper"}"#.utf8)
        let first = try #require(PlanItemCache.item(for: payload))
        #expect(first.kind == "upper")
        #expect(PlanItemCache.item(for: payload)?.title == "Upper")
        // a different payload is a different entry
        #expect(PlanItemCache.item(for: Data(#"{"date": "2026-10-27", "kind": "rest"}"#.utf8))?.kind == "rest")
        #expect(PlanItemCache.item(for: Data("not json".utf8)) == nil)
    }
}
