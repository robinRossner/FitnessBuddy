import XCTest

/// Navigation and key flows driven by real taps (`make ios-flows`; every other screen is rendered in-process by the
/// FitnessBuddySnapshots target).
/// `testNavigationWalk` takes no pictures: it is the proof that tabs, pushes and sheets work when tapped. The flow
/// tests shoot the states in between (`flow-*.png`). The clock is frozen (`-uiFixedNow`), so a workout started by
/// taps starts at the frozen instant and its timers show fixed values.
@MainActor
final class FlowsScreens: ScreenpassCase {
    // MARK: Navigation walk

    /// All five tabs through the tab bar; in each one a pushed screen (and back) and a sheet (and closed again).
    func testNavigationWalk() {
        launch(tab: "summary")
        waitForScreen(AXID.Screen.summary)

        // Summary: the planned lift is pushed, Settings is a sheet with a push inside it (Manage gyms).
        push(tap: AXID.Summary.nextLiftCard, expecting: AXID.Summary.plannedLift)
        pop(from: AXID.Summary.plannedLift, to: AXID.Screen.summary)
        push(tap: AXID.Summary.settingsButton, expecting: AXID.Summary.settings)
        push(tap: AXID.Summary.manageGymsRow, expecting: AXID.Summary.gymManage)
        app.navigationBars["Manage gyms"].buttons.element(boundBy: 0).tap()
        XCTAssertTrue(element(AXID.Summary.gymManage).waitForNonExistence(timeout: 10), "screenpass: Manage gyms did not go away")
        tap(AXID.Summary.settingsDone)
        XCTAssertTrue(element(AXID.Summary.settings).waitForNonExistence(timeout: 10), "screenpass: Settings did not close")

        // Workout: the plan preview is pushed, the chooser is a sheet.
        openTab("Workout", expecting: AXID.Screen.workout)
        push(tap: AXID.Workout.nextCard, expecting: AXID.Workout.plan)
        pop(from: AXID.Workout.plan, to: AXID.Screen.workout)
        sheet(tap: AXID.Workout.chooseButton, expecting: AXID.Workout.chooser, close: AXID.Workout.chooserCancel)

        // Progress: a lift's detail is pushed, All lifts is a sheet.
        openTab("Progress", expecting: AXID.Screen.progress)
        push(tapFirstWithPrefix: "progress.liftCard.", expecting: AXID.Progress.liftDetail)
        pop(from: AXID.Progress.liftDetail, to: AXID.Screen.progress)
        sheet(tap: AXID.Progress.allLiftsButton, expecting: AXID.Progress.allLifts, close: AXID.Progress.allLiftsDone)

        // Health: the HRV chart is pushed, the readiness details are a sheet.
        openTab("Health", expecting: AXID.Screen.health)
        push(tap: AXID.Health.signalCard("hrv"), expecting: AXID.Health.metricDetail)
        pop(from: AXID.Health.metricDetail, to: AXID.Screen.health)
        sheet(tap: AXID.Health.readinessCard, expecting: AXID.Health.readiness, close: AXID.Health.readinessDone)

        // Coach: Coach Settings is pushed, the Ask sheet opens.
        openTab("Coach", expecting: AXID.Screen.coach)
        push(tap: AXID.Coach.settingsButton, expecting: AXID.Coach.settings)
        pop(from: AXID.Coach.settings, to: AXID.Screen.coach)
        sheet(tap: AXID.Coach.askButton, expecting: AXID.Coach.askSheet, close: AXID.Coach.askCancel)

        // and back to the first tab
        openTab("Summary", expecting: AXID.Screen.summary)
    }

    // MARK: Workout

    /// Plan, start, log the first set, finish: the whole lifting path through taps.
    func testWorkoutFlow() {
        launch(tab: "workout")
        waitForScreen(AXID.Screen.workout)

        push(tap: AXID.Workout.nextCard, expecting: AXID.Workout.plan)
        tap(AXID.Workout.planStart)
        waitForScreen(AXID.Workout.active)

        let header = element(AXID.Workout.setsHeader)
        XCTAssertTrue(header.waitForExistence(timeout: 10), "screenpass: sets header not found")
        XCTAssertTrue(header.label.contains("0/"), "screenpass: a new workout should start with 0 sets logged, got '\(header.label)'")
        XCTAssertFalse(element(AXID.Workout.restBar).exists, "screenpass: no rest bar before a set is logged")
        snap("flow-workout-started")

        let check = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", AXID.Workout.setCheckPrefix)).firstMatch
        XCTAssertTrue(check.waitForExistence(timeout: 10), "screenpass: no set check button found")
        check.tap()
        XCTAssertTrue(element(AXID.Workout.restBar).waitForExistence(timeout: 10), "screenpass: the rest bar did not appear after logging a set")
        expectLabel(of: AXID.Workout.setsHeader, toContain: "1/")
        snap("flow-workout-set-logged")

        tap(AXID.Workout.finishButton)
        let choice = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Finish and drop' OR label BEGINSWITH 'Finish workout'")).firstMatch
        XCTAssertTrue(choice.waitForExistence(timeout: 10), "screenpass: the finish confirmation did not appear")
        choice.tap()
        waitForScreen(AXID.Workout.summary)
        XCTAssertTrue(element(AXID.Workout.active).waitForNonExistence(timeout: 10), "screenpass: the workout is still running after Finish")
        snap("flow-workout-summary")

        tap(AXID.Workout.summaryDone)
        XCTAssertTrue(element(AXID.Workout.summary).waitForNonExistence(timeout: 10), "screenpass: the summary sheet did not close")
        waitForScreen(AXID.Screen.workout)
        XCTAssertTrue(element(AXID.Workout.chooseButton).waitForExistence(timeout: 10), "screenpass: the Workout tab should be idle again")
    }

    // MARK: Health

    /// Log weight sheet: type a value, save, and see it on the Health screen.
    func testLogWeightFlow() {
        launch(tab: "health")
        waitForScreen(AXID.Screen.health)

        tap(AXID.Health.logWeightButton)
        waitForScreen(AXID.Health.logWeight)
        let field = element(AXID.Health.logWeightField)
        XCTAssertTrue(field.waitForExistence(timeout: 10), "screenpass: weight field not found")
        field.tap()
        // select the prefilled value and replace it with whole kilograms (no decimal separator to depend on the keyboard layout)
        field.press(forDuration: 1.0)
        let selectAll = app.menuItems["Select All"]
        if selectAll.waitForExistence(timeout: 2) { selectAll.tap() }
        field.typeText("79")
        XCTAssertEqual(field.value as? String, "79", "screenpass: weight field should hold the typed value")
        // Save sits in the navigation bar, above the decimal pad (which has no return key); the keyboard goes with the sheet
        element(AXID.Health.logWeightSave).tap()
        XCTAssertTrue(element(AXID.Health.logWeight).waitForNonExistence(timeout: 10), "screenpass: the weight sheet did not close")
        waitForScreen(AXID.Screen.health)

        // the new entry shows in the entries card and as the latest bodyweight value
        let entries = element(AXID.Health.weighInEntries)
        XCTAssertTrue(entries.waitForExistence(timeout: 10), "screenpass: the new weigh-in did not show up")
        let card = element(AXID.Health.signalCard("bodyweight"))
        XCTAssertTrue(card.waitForExistence(timeout: 10), "screenpass: bodyweight card not found")
        let latest = NSPredicate(format: "value CONTAINS 'Latest 79'")
        expectation(for: latest, evaluatedWith: card)
        waitForExpectations(timeout: 10)
        for _ in 0..<5 { app.swipeUp() }
        snap("flow-weight-saved")
    }

    // MARK: Coach

    /// Ask the coach a question; the demo coach answers a few seconds later on the proposal screen it opens.
    func testAskCoachFlow() {
        launch(tab: "coach")
        waitForScreen(AXID.Screen.coach)

        tap(AXID.Coach.askButton)
        waitForScreen(AXID.Coach.askSheet)
        let field = element(AXID.Coach.askField)
        XCTAssertTrue(field.waitForExistence(timeout: 10), "screenpass: question field not found")
        focus(field)
        field.typeText("How are my easy runs going?")
        element(AXID.Coach.askSend).tap()

        XCTAssertTrue(element(AXID.Coach.askSheet).waitForNonExistence(timeout: 15), "screenpass: the Ask sheet did not close after sending")
        waitForScreen(AXID.Coach.proposalDetail)
        // the answer arrives when the proposal leaves "Coach Is Thinking": then it can be approved
        XCTAssertTrue(element(AXID.Coach.detailApprove).waitForExistence(timeout: 40), "screenpass: the coach did not answer")
        snap("flow-coach-answer")
    }

    /// Approve the first proposal from its card on the Coach tab: the review count drops and a result card shows.
    func testCoachApproveFlow() {
        launch(tab: "coach")
        waitForScreen(AXID.Screen.coach)

        let approves = app.buttons.matching(identifier: AXID.Coach.cardApprove)
        XCTAssertTrue(approves.firstMatch.waitForExistence(timeout: 15), "screenpass: no proposal to approve")
        XCTAssertEqual(approves.count, 2, "screenpass: the demo has two proposals to review")
        // the tab badge is not exposed to accessibility; flow-coach-approved.png shows it

        approves.firstMatch.tap()
        XCTAssertTrue(element(AXID.Coach.decisionResult).waitForExistence(timeout: 15), "screenpass: no result card after approving")
        XCTAssertEqual(approves.count, 1, "screenpass: one proposal should be left to review")
        snap("flow-coach-approved")
    }
}

// MARK: - Helpers

private extension FlowsScreens {
    func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }

    /// Scrolls down (at most five swipes) until the element can be tapped, then taps it.
    func tap(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
        tap(element(id), named: id, file: file, line: line)
    }

    func tap(_ target: XCUIElement, named name: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(target.waitForExistence(timeout: 15), "screenpass: '\(name)' not found", file: file, line: line)
        var swipes = 0
        while !target.isHittable, swipes < 5 {
            app.swipeUp()
            swipes += 1
        }
        target.tap()
    }

    func openTab(_ name: String, expecting id: String, file: StaticString = #filePath, line: UInt = #line) {
        let button = app.tabBars.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", name)).firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 15), "screenpass: tab '\(name)' not found", file: file, line: line)
        button.tap()
        waitForScreen(id, file: file, line: line)
    }

    /// Taps `id`, the pushed screen `expecting` shows up.
    func push(tap id: String, expecting screen: String, file: StaticString = #filePath, line: UInt = #line) {
        tap(id, file: file, line: line)
        waitForScreen(screen, file: file, line: line)
    }

    func push(tapFirstWithPrefix prefix: String, expecting screen: String, file: StaticString = #filePath, line: UInt = #line) {
        tap(app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", prefix)).firstMatch,
            named: prefix + "*", file: file, line: line)
        waitForScreen(screen, file: file, line: line)
    }

    /// Back button: the pushed screen is gone, the one underneath is back.
    func pop(from pushed: String, to root: String, file: StaticString = #filePath, line: UInt = #line) {
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(element(pushed).waitForNonExistence(timeout: 10), "screenpass: '\(pushed)' did not go away", file: file, line: line)
        waitForScreen(root, file: file, line: line)
    }

    /// Opens a sheet through `id`, then closes it with its button.
    func sheet(tap id: String, expecting screen: String, close: String, file: StaticString = #filePath, line: UInt = #line) {
        tap(id, file: file, line: line)
        waitForScreen(screen, file: file, line: line)
        tap(close, file: file, line: line)
        XCTAssertTrue(element(screen).waitForNonExistence(timeout: 10), "screenpass: sheet '\(screen)' did not close", file: file, line: line)
    }

    func expectLabel(of id: String, toContain text: String, file: StaticString = #filePath, line: UInt = #line) {
        let target = element(id)
        let done = NSPredicate(format: "label CONTAINS %@", text)
        expectation(for: done, evaluatedWith: target)
        waitForExpectations(timeout: 10) { error in
            if error != nil { XCTFail("screenpass: '\(id)' label never contained '\(text)', is '\(target.label)'", file: file, line: line) }
        }
    }

    /// Taps a text field until the keyboard is up (the first tap can land while a sheet is still settling).
    func focus(_ field: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        for _ in 0..<4 {
            field.tap()
            if app.keyboards.firstMatch.waitForExistence(timeout: 3) { return }
        }
        XCTFail("screenpass: the keyboard did not come up", file: file, line: line)
    }
}
