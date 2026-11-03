import XCTest

/// Every screen reachable from the Health tab in demo mode.
/// Deep links: `-uiSheet readiness|weight` and `-uiRoute metric/<id>` (`hrv`, `rhr`, `sleep`, `steps`, `bodyweight`).
@MainActor
final class HealthScreens: ScreenpassCase {
    func testReadiness() {
        launch(tab: "health", extra: ["-uiSheet", "readiness"])
        waitForScreen(AXID.Health.readiness)
        snap("health-readiness")
    }

    func testLogWeight() {
        launch(tab: "health", extra: ["-uiSheet", "weight"])
        waitForScreen(AXID.Health.logWeight)
        snap("health-log-weight")
    }

    func testMetricHRV() {
        launch(tab: "health", extra: ["-uiRoute", "metric/hrv"])
        waitForScreen(AXID.Health.metricDetail)
        snap("health-metric-hrv")
    }

    func testMetricSleep() {
        launch(tab: "health", extra: ["-uiRoute", "metric/sleep"])
        waitForScreen(AXID.Health.metricDetail)
        snap("health-metric-sleep")
    }

    func testMetricBodyweight() {
        launch(tab: "health", extra: ["-uiRoute", "metric/bodyweight"])
        waitForScreen(AXID.Health.metricDetail)
        snap("health-metric-bodyweight")
    }

    /// Real taps from the tab root: a pushed screen (HRV chart), then a sheet (Readiness).
    func testNavigationHealth() {
        launch(tab: "health")
        waitForScreen(AXID.Screen.health)
        tap(AXID.Health.signalCard("hrv"))
        waitForScreen(AXID.Health.metricDetail)
        app.navigationBars.buttons.firstMatch.tap()
        waitForScreen(AXID.Screen.health)
        tap(AXID.Health.readinessCard)
        waitForScreen(AXID.Health.readiness)
    }
}

private extension HealthScreens {
    /// Taps the element with this identifier, scrolling down a little when it is off screen.
    func tap(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
        let element = app.descendants(matching: .any)[id].firstMatch
        XCTAssertTrue(element.waitForExistence(timeout: 15), "screenpass: '\(id)' not found", file: file, line: line)
        var swipes = 0
        while !element.isHittable, swipes < 5 {
            app.swipeUp()
            swipes += 1
        }
        element.tap()
    }
}
