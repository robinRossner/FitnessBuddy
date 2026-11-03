import XCTest

/// Progress tab: the Running and Insights segments, every pushed screen and both sheets. Each screen is opened with
/// the DEBUG launch arguments of `ProgressTabView` (honoured only with `-uiTab progress`); `testNavigation` taps
/// through for real instead.
@MainActor
final class ProgressScreens: ScreenpassCase {
    private func shot(_ name: String, screen: String, extra: [String]) {
        launch(tab: "progress", extra: extra)
        waitForScreen(screen)
        snap(name)
    }

    func testRunning() {
        shot("progress-running", screen: AXID.Progress.running, extra: ["-uiSegment", "running"])
    }

    func testInsights() {
        shot("progress-insights", screen: AXID.Progress.insights, extra: ["-uiSegment", "insights"])
    }

    func testLiftDetail() {
        shot("progress-lift-detail", screen: AXID.Progress.liftDetail, extra: ["-uiLift", "101"])
    }

    func testRunAnalysis() {
        shot("progress-run-analysis", screen: AXID.Progress.runAnalysis, extra: ["-uiRun", "run-i0"])
    }

    func testVdot() {
        shot("progress-vdot", screen: AXID.Progress.vdot, extra: ["-uiRoute", "vdot"])
    }

    func testEfficiency() {
        shot("progress-efficiency", screen: AXID.Progress.efficiency, extra: ["-uiRoute", "efficiency"])
    }

    func testAllRuns() {
        shot("progress-all-runs", screen: AXID.Progress.allRuns, extra: ["-uiRoute", "allruns"])
    }

    func testInsightsScreen() {
        shot("progress-insights-screen", screen: AXID.Progress.insightsScreen, extra: ["-uiRoute", "insights"])
    }

    func testInsightDetail() {
        shot("progress-insight-detail", screen: AXID.Progress.insightDetail, extra: ["-uiRoute", "insight"])
    }

    func testAllLifts() {
        shot("progress-all-lifts", screen: AXID.Progress.allLifts, extra: ["-uiSheet", "alllifts"])
    }

    func testRunDetail() {
        shot("progress-run-detail", screen: AXID.Progress.runDetail, extra: ["-uiSegment", "running", "-uiSheet", "rundetail"])
    }

    /// Real taps from the tab root: the All Lifts sheet (and back), then a lift card pushes the lift's detail.
    func testNavigationLiftsSheetAndDetail() {
        launch(tab: "progress")
        waitForScreen(AXID.Screen.progress)

        tapScrolling(element(AXID.Progress.allLiftsButton))
        waitForScreen(AXID.Progress.allLifts)
        element(AXID.Progress.allLiftsDone).tap()
        XCTAssertTrue(element(AXID.Progress.allLifts).waitForNonExistence(timeout: 10), "All Lifts sheet did not close")

        let cardPrefix = String(AXID.Progress.liftCard(0).dropLast())
        tapScrolling(app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH %@", cardPrefix)).firstMatch)
        waitForScreen(AXID.Progress.liftDetail)
    }

    private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    /// Swipes up until the element is on screen, then taps it.
    private func tapScrolling(_ target: XCUIElement) {
        var swipes = 0
        while !(target.exists && target.isHittable) && swipes < 8 {
            app.swipeUp()
            swipes += 1
        }
        XCTAssertTrue(target.exists && target.isHittable, "progress: control never scrolled into view")
        target.tap()
    }
}
