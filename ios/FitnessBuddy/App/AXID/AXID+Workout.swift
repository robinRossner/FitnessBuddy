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
    }
}
