@testable import FitnessBuddy

/// One screen of the in-process snapshot pass: the PNG name, the tab it belongs to (`uiTab`, also the `ONLY=` filter),
/// the deep-link values (the same keys and values as the launch arguments, without the dash) and the accessibility
/// identifier that proves the intended screen is showing.
struct SnapshotScreen: Sendable {
    let name: String
    let tab: String
    let args: [String: String]
    let ready: String

    init(_ name: String, tab: String, _ args: [String: String] = [:], ready: String) {
        self.name = name
        self.tab = tab
        self.args = args
        self.ready = ready
    }
}

/// Every screen of `make ios-screens`. The deep links are those of the tabs' DEBUG hooks:
/// `SummaryDebugRoute`, `WorkoutUIHooks`, `ProgressLaunch`, `HealthTabView`, `CoachLaunch`.
enum SnapshotRegistry {
    static let all: [SnapshotScreen] = tabs + summary + health + workout + progress + coach

    static let tabs: [SnapshotScreen] = [
        .init("tab-summary", tab: "summary", ready: AXID.Screen.summary),
        .init("tab-workout", tab: "workout", ready: AXID.Screen.workout),
        .init("tab-progress", tab: "progress", ready: AXID.Screen.progress),
        .init("tab-health", tab: "health", ready: AXID.Screen.health),
        .init("tab-coach", tab: "coach", ready: AXID.Screen.coach),
    ]

    /// `uiSheet settings|pins`, `uiRoute plannedlift|metric/<id>|settings/gyms|settings/machines|settings/machines/editor`.
    static let summary: [SnapshotScreen] = [
        .init("summary-settings", tab: "summary", ["uiSheet": "settings"], ready: AXID.Summary.settings),
        .init("summary-settings-withings", tab: "summary", ["uiRoute": "settings/withings"], ready: AXID.Summary.withingsStatus),
        .init("summary-settings-withings-error", tab: "summary", ["uiRoute": "settings/withings-error"], ready: AXID.Summary.withingsStatus),
        .init("summary-settings-withings-disconnected", tab: "summary", ["uiRoute": "settings/withings-disconnected"], ready: AXID.Summary.withingsStatus),
        .init("summary-settings-withings-off", tab: "summary", ["uiRoute": "settings/withings-off"], ready: AXID.Summary.withingsStatus),
        .init("summary-gym-manage", tab: "summary", ["uiRoute": "settings/gyms"], ready: AXID.Summary.gymManage),
        .init("summary-gym-machines", tab: "summary", ["uiRoute": "settings/machines"], ready: AXID.Summary.gymMachines),
        .init("summary-machine-editor", tab: "summary", ["uiRoute": "settings/machines/editor"], ready: AXID.Summary.machineEditor),
        .init("summary-pin-editor", tab: "summary", ["uiSheet": "pins"], ready: AXID.Summary.pinEditor),
        .init("summary-planned-lift", tab: "summary", ["uiRoute": "plannedlift"], ready: AXID.Summary.plannedLift),
        .init("summary-metric-hrv", tab: "summary", ["uiRoute": "metric/hrv"], ready: AXID.Summary.metricDetail),
        .init("summary-metric-volume", tab: "summary", ["uiRoute": "metric/volume"], ready: AXID.Summary.metricDetail),
    ]

    /// `uiSheet readiness|weight`, `uiRoute metric/<id>`.
    static let health: [SnapshotScreen] = [
        .init("health-readiness", tab: "health", ["uiSheet": "readiness"], ready: AXID.Health.readiness),
        .init("health-log-weight", tab: "health", ["uiSheet": "weight"], ready: AXID.Health.logWeight),
        .init("health-metric-hrv", tab: "health", ["uiRoute": "metric/hrv"], ready: AXID.Health.metricDetail),
        .init("health-metric-sleep", tab: "health", ["uiRoute": "metric/sleep"], ready: AXID.Health.metricDetail),
        .init("health-metric-bodyweight", tab: "health", ["uiRoute": "metric/bodyweight"], ready: AXID.Health.metricDetail),
    ]

    /// `uiRoute plan|detail`, `uiSheet chooser`; `uiWorkout active|summary` starts a demo workout 23 min before the
    /// frozen clock, with `uiSheet rest|picker|newExercise|exerciseSetup|edit|reorder` for the sheets over it.
    static let workout: [SnapshotScreen] = [
        .init("workout-plan", tab: "workout", ["uiRoute": "plan"], ready: AXID.Workout.plan),
        .init("workout-chooser", tab: "workout", ["uiSheet": "chooser"], ready: AXID.Workout.chooser),
        .init("workout-detail", tab: "workout", ["uiRoute": "detail"], ready: AXID.Workout.detail),
        .init("workout-active", tab: "workout", ["uiWorkout": "active"], ready: AXID.Workout.active),
        .init("workout-rest-picker", tab: "workout", ["uiWorkout": "active", "uiSheet": "rest"], ready: AXID.Workout.restPicker),
        .init("workout-exercise-picker", tab: "workout", ["uiWorkout": "active", "uiSheet": "picker"], ready: AXID.Workout.exercisePicker),
        .init("workout-new-exercise", tab: "workout", ["uiWorkout": "active", "uiSheet": "newExercise"], ready: AXID.Workout.newExercise),
        .init("workout-exercise-setup", tab: "workout", ["uiWorkout": "active", "uiSheet": "exerciseSetup"], ready: AXID.Workout.exerciseSetup),
        .init("workout-exercise-edit", tab: "workout", ["uiWorkout": "active", "uiSheet": "edit"], ready: AXID.Workout.exerciseEdit),
        .init("workout-reorder", tab: "workout", ["uiWorkout": "active", "uiSheet": "reorder"], ready: AXID.Workout.reorder),
        .init("workout-summary", tab: "workout", ["uiWorkout": "summary"], ready: AXID.Workout.summary),
    ]

    /// `uiSegment running|insights`, `uiLift <variant>`, `uiRun <id>`, `uiRoute vdot|efficiency|allruns|insights|insight`,
    /// `uiSheet alllifts|rundetail|rundetail-easy`.
    static let progress: [SnapshotScreen] = [
        .init("progress-running", tab: "progress", ["uiSegment": "running"], ready: AXID.Progress.running),
        .init("progress-insights", tab: "progress", ["uiSegment": "insights"], ready: AXID.Progress.insights),
        .init("progress-lift-detail", tab: "progress", ["uiLift": "101"], ready: AXID.Progress.liftDetail),
        .init("progress-run-analysis", tab: "progress", ["uiRun": "run-i0"], ready: AXID.Progress.runAnalysis),
        .init("progress-vdot", tab: "progress", ["uiRoute": "vdot"], ready: AXID.Progress.vdot),
        .init("progress-efficiency", tab: "progress", ["uiRoute": "efficiency"], ready: AXID.Progress.efficiency),
        .init("progress-all-runs", tab: "progress", ["uiRoute": "allruns"], ready: AXID.Progress.allRuns),
        .init("progress-insights-screen", tab: "progress", ["uiRoute": "insights"], ready: AXID.Progress.insightsScreen),
        .init("progress-insight-detail", tab: "progress", ["uiRoute": "insight"], ready: AXID.Progress.insightDetail),
        .init("progress-all-lifts", tab: "progress", ["uiSheet": "alllifts"], ready: AXID.Progress.allLifts),
        .init("progress-run-detail", tab: "progress", ["uiSegment": "running", "uiSheet": "rundetail"], ready: AXID.Progress.runDetail),
        .init("progress-run-detail-easy", tab: "progress", ["uiSegment": "running", "uiSheet": "rundetail-easy"], ready: AXID.Progress.runDetail),
    ]

    /// `uiRoute proposal:<id>|history|settings|proposals`, `uiSheet ask` (no keyboard: nothing takes focus).
    static let coach: [SnapshotScreen] = [
        .init("coach-proposal-detail", tab: "coach", ["uiRoute": "proposal:demo-p1"], ready: AXID.Coach.proposalDetail),
        .init("coach-history", tab: "coach", ["uiRoute": "history"], ready: AXID.Coach.history),
        .init("coach-settings", tab: "coach", ["uiRoute": "settings"], ready: AXID.Coach.settings),
        .init("coach-proposal-list", tab: "coach", ["uiRoute": "proposals"], ready: AXID.Coach.proposalList),
        .init("coach-ask-sheet", tab: "coach", ["uiSheet": "ask"], ready: AXID.Coach.askSheet),
    ]
}
