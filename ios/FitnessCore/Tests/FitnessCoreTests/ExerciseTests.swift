import Foundation
import Testing
@testable import FitnessCore

// MARK: - Today

@Suite(.tags(.workout, .fast))
struct TodayTitleTests {
    @Test func liftingCardsOnlyNameTheHalf() {
        #expect(PlanFormat.cardTitle(type: "lifting", kind: "upper", title: "Upper (deload)") == "Upper body")
        #expect(PlanFormat.cardTitle(type: "lifting", kind: "lower", title: nil) == "Lower body")
        #expect(PlanFormat.cardTitle(type: "easy_run", kind: "easy_run", title: nil) == "Easy run")
    }
}
