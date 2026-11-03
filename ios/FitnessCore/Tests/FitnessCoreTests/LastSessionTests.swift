import Foundation
import Testing
@testable import FitnessCore

@Suite(.tags(.workout, .fast))
struct LastSessionTests {
    typealias V = LastSession.Values

    @Test func settingDefaultsToShown() throws {
        let d = try #require(UserDefaults(suiteName: "LastSessionTests-\(UUID().uuidString)"))
        #expect(LastSession.isShown(d))
        d.set(false, forKey: LastSession.defaultsKey)
        #expect(!LastSession.isShown(d))
        d.set(true, forKey: LastSession.defaultsKey)
        #expect(LastSession.isShown(d))
    }

    @Test func captionShowsTargetAndLastWhenOn() {
        let c = LastSession.caption(target: V(weight: 50, reps: 8), last: V(weight: 47, reps: 8), showLast: true)
        #expect(c == "Target 50 × 8  ·  Last 47 × 8")
    }

    @Test func captionDropsLastWhenOff() {
        let c = LastSession.caption(target: V(weight: 50, reps: 8), last: V(weight: 47, reps: 8), showLast: false)
        #expect(c == "Target 50 × 8")
    }

    @Test func captionIsEmptyWithoutTargetWhenOff() {
        #expect(LastSession.caption(target: V(), last: V(weight: 47, reps: 8), showLast: false) == nil)
        #expect(LastSession.caption(target: V(), last: V(weight: 47, reps: 8), showLast: true) == "Last 47 × 8")
        #expect(LastSession.caption(target: V(), last: V(), showLast: true) == nil)
    }

    @Test func timedValuesShowSeconds() {
        #expect(LastSession.caption(target: V(duration: 45), last: V(duration: 40), showLast: true) == "Target 45 s  ·  Last 40 s")
        #expect(LastSession.caption(target: V(duration: 45), last: V(duration: 40), showLast: false) == "Target 45 s")
    }

    @Test func hiddenPrefillUsesTargetWhenKnown() {
        let p = LastSession.hiddenPrefill(target: V(weight: 50, reps: 8), last: V(weight: 60, reps: 12), repRangeHigh: 10)
        #expect(p == V(weight: 50, reps: 8))
        // a target without reps falls back to the top of the rep range, never to last session's reps
        let q = LastSession.hiddenPrefill(target: V(weight: 50), last: V(weight: 60, reps: 12), repRangeHigh: 10)
        #expect(q == V(weight: 50, reps: 10))
    }

    @Test func hiddenPrefillWithoutTargetLeavesWeightEmpty() {
        let p = LastSession.hiddenPrefill(target: nil, last: V(weight: 60, reps: 12), repRangeHigh: 10)
        #expect(p == V(weight: nil, reps: 10))
        #expect(LastSession.hiddenPrefill(target: V(), last: V(weight: 60, reps: 12), repRangeHigh: nil) == V())
    }

    @Test func hiddenPrefillKeepsTimedSetsTimed() {
        let p = LastSession.hiddenPrefill(target: nil, last: V(weight: 0, duration: 40), repRangeHigh: nil)
        #expect(p == V(duration: 40))
    }

    @Test func prefillWeightIgnoresLastWhenHidden() {
        #expect(SetupRules.prefillWeight(target: nil, last: 60, baseline: false, showLast: false) == nil)
        #expect(SetupRules.prefillWeight(target: 50, last: 60, baseline: false, showLast: false) == 50)
        #expect(SetupRules.prefillWeight(target: nil, last: 60, baseline: false, showLast: true) == 60)
        #expect(SetupRules.prefillWeight(target: 50, last: 60, baseline: true, showLast: false) == nil)
    }
}
