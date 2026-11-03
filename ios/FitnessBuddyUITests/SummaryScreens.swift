import XCTest

/// Every screen reachable from the Summary tab in demo mode (RunDetail and Insights belong to the Progress pass).
/// Deep links: `-uiSheet settings|pins` and `-uiRoute plannedlift|metric/<id>|settings/gyms|settings/machines|settings/machines/editor`.
@MainActor
final class SummaryScreens: ScreenpassCase {
    func testSettings() {
        launch(tab: "summary", extra: ["-uiSheet", "settings"])
        waitForScreen(AXID.Summary.settings)
        snap("summary-settings")
    }

    func testGymManage() {
        launch(tab: "summary", extra: ["-uiRoute", "settings/gyms"])
        waitForScreen(AXID.Summary.gymManage)
        snap("summary-gym-manage")
    }

    func testGymMachines() {
        launch(tab: "summary", extra: ["-uiRoute", "settings/machines"])
        waitForScreen(AXID.Summary.gymMachines)
        snap("summary-gym-machines")
    }

    func testMachineEditor() {
        launch(tab: "summary", extra: ["-uiRoute", "settings/machines/editor"])
        waitForScreen(AXID.Summary.machineEditor)
        snap("summary-machine-editor")
    }

    func testPinEditor() {
        launch(tab: "summary", extra: ["-uiSheet", "pins"])
        waitForScreen(AXID.Summary.pinEditor)
        snap("summary-pin-editor")
    }

    func testPlannedLift() {
        launch(tab: "summary", extra: ["-uiRoute", "plannedlift"])
        waitForScreen(AXID.Summary.plannedLift)
        snap("summary-planned-lift")
    }

    func testMetricHRV() {
        launch(tab: "summary", extra: ["-uiRoute", "metric/hrv"])
        waitForScreen(AXID.Summary.metricDetail)
        snap("summary-metric-hrv")
    }

    func testMetricVolume() {
        launch(tab: "summary", extra: ["-uiRoute", "metric/volume"])
        waitForScreen(AXID.Summary.metricDetail)
        snap("summary-metric-volume")
    }

    /// Real taps from the tab root: a pushed screen (next lift), then a sheet (Settings) with a push inside it (Manage gyms).
    func testNavigationSummary() {
        launch(tab: "summary")
        waitForScreen(AXID.Screen.summary)
        tap(AXID.Summary.nextLiftCard)
        waitForScreen(AXID.Summary.plannedLift)
        app.navigationBars.buttons.firstMatch.tap()
        waitForScreen(AXID.Screen.summary)
        tap(AXID.Summary.settingsButton)
        waitForScreen(AXID.Summary.settings)
        tap(AXID.Summary.manageGymsRow)
        waitForScreen(AXID.Summary.gymManage)
    }
}

private extension SummaryScreens {
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
