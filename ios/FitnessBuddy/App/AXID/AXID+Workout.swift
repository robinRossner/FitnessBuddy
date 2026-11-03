// Accessibility identifiers of the Workout tab's screens and the controls the navigation test taps.
extension AXID {
    enum Workout {
        /// Plan preview ("View Plan & Start"), pushed.
        static let plan = "workout.plan"
        /// A finished workout, pushed.
        static let detail = "workout.detail"
        /// The running workout (replaces the start screen).
        static let active = "workout.active"
        static let chooser = "workout.chooser"
        static let summary = "workout.summary"
        static let restPicker = "workout.restPicker"
        static let exercisePicker = "workout.exercisePicker"
        static let newExercise = "workout.newExercise"
        static let exerciseSetup = "workout.exerciseSetup"
        static let exerciseEdit = "workout.exerciseEdit"
        static let reorder = "workout.reorder"
        /// Tappable controls on the start screen.
        static let nextCard = "workout.nextCard"
        static let chooseButton = "workout.chooseButton"
        static let chooserCancel = "workout.chooserCancel"
        /// "Start Workout" on the plan preview.
        static let planStart = "workout.planStart"
        /// The Sets / Volume tiles at the top of the running workout (one element, label like "Sets, 1/18, Volume, ...").
        static let setsHeader = "workout.setsHeader"
        /// The check button of a set row: `workout.setCheck.<exerciseOrder>.<setOrder>`.
        static let setCheckPrefix = "workout.setCheck."
        static func setCheck(exercise: Int, set: Int) -> String { "\(setCheckPrefix)\(exercise).\(set)" }
        static let finishButton = "workout.finishButton"
        /// "Done" on the workout summary sheet.
        static let summaryDone = "workout.summaryDone"
        /// The rest bar shown after a set is logged.
        static let restBar = "workout.restBar"
    }
}
