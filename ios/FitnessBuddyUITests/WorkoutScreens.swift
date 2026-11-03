import XCTest

/// Every screen reachable from the Workout tab in demo mode (launch hooks: `-uiRoute`, `-uiSheet`, `-uiWorkout`,
/// see `WorkoutUIHooks`). The clock is frozen, so a running workout started "23 min ago" always shows 23:00.
@MainActor
final class WorkoutScreens: ScreenpassCase {
    private func shot(_ id: String, _ name: String, extra: [String]) {
        launch(tab: "workout", extra: extra)
        waitForScreen(id)
        snap(name)
    }

    // MARK: Before starting

    func testPlan() {
        shot(AXID.Workout.plan, "workout-plan", extra: ["-uiRoute", "plan"])
    }

    func testChooser() {
        shot(AXID.Workout.chooser, "workout-chooser", extra: ["-uiSheet", "chooser"])
    }

    func testDetail() {
        shot(AXID.Workout.detail, "workout-detail", extra: ["-uiRoute", "detail"])
    }

    // MARK: Running workout

    func testActive() {
        shot(AXID.Workout.active, "workout-active", extra: ["-uiWorkout", "active"])
    }

    func testRestPicker() {
        shot(AXID.Workout.restPicker, "workout-rest-picker", extra: ["-uiWorkout", "active", "-uiSheet", "rest"])
    }

    func testExercisePicker() {
        shot(AXID.Workout.exercisePicker, "workout-exercise-picker", extra: ["-uiWorkout", "active", "-uiSheet", "picker"])
    }

    func testNewExercise() {
        shot(AXID.Workout.newExercise, "workout-new-exercise", extra: ["-uiWorkout", "active", "-uiSheet", "newExercise"])
    }

    func testExerciseSetup() {
        shot(AXID.Workout.exerciseSetup, "workout-exercise-setup", extra: ["-uiWorkout", "active", "-uiSheet", "exerciseSetup"])
    }

    func testExerciseEdit() {
        shot(AXID.Workout.exerciseEdit, "workout-exercise-edit", extra: ["-uiWorkout", "active", "-uiSheet", "edit"])
    }

    func testReorder() {
        shot(AXID.Workout.reorder, "workout-reorder", extra: ["-uiWorkout", "active", "-uiSheet", "reorder"])
    }

    func testSummary() {
        shot(AXID.Workout.summary, "workout-summary", extra: ["-uiWorkout", "summary"])
    }

    // MARK: Real navigation

    /// From the tab root: the plan preview through the next-workout card (push), then back and the chooser sheet through
    /// "Choose a Workout".
    func testNavigation() {
        launch(tab: "workout")
        waitForScreen(AXID.Screen.workout)

        let next = app.descendants(matching: .any)[AXID.Workout.nextCard].firstMatch
        XCTAssertTrue(next.waitForExistence(timeout: 15), "screenpass: next-workout card not found")
        next.tap()
        waitForScreen(AXID.Workout.plan)
        app.navigationBars.buttons.firstMatch.tap()

        let choose = app.buttons[AXID.Workout.chooseButton].firstMatch
        XCTAssertTrue(choose.waitForExistence(timeout: 15), "screenpass: 'Choose a Workout' not found")
        if !choose.isHittable { app.swipeUp() }
        choose.tap()
        waitForScreen(AXID.Workout.chooser)
    }
}
