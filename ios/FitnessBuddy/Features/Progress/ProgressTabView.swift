import SwiftUI

/// Progress tab: Lifting | Running, each a scroll of chart cards. Data comes from `StatsRepository` (cached, offline-safe).
struct ProgressTabView: View {
    enum Segment: String, CaseIterable, Identifiable {
        case lifting = "Lifting", running = "Running"
        var id: String { rawValue }
    }

    @State private var segment: Segment = .lifting
    @State private var openRun: RunRoute?
    @State private var path = NavigationPath()

    #if DEBUG
    /// Launch arguments for screenshots / UI runs: `-uiSegment running`, `-uiLift <variant id>`, `-uiRun <run id>`.
    init() {
        let d = UserDefaults.standard
        _segment = State(initialValue: d.string(forKey: "uiSegment") == "running" ? .running : .lifting)
        var p = NavigationPath()
        if let id = d.string(forKey: "uiLift").flatMap(Int.init) { p.append(LiftSummaryDTO(variantId: id, name: "Lift")) }
        if let id = d.string(forKey: "uiRun") { p.append(RunRoute(id: id)) }
        if d.string(forKey: "uiSheet") == "insights" { p.append(InsightsRoute()) }
        _path = State(initialValue: p)
    }
    #endif

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                switch segment {
                case .lifting: LiftingProgressView()
                case .running: RunningProgressView(openRun: $openRun)
                }
            }
            .background(Color.screenBackground)
            .navigationTitle("Progress")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink(value: InsightsRoute()) { Image(systemName: "lightbulb") }
                        .accessibilityLabel("Insights")
                }
                ToolbarItem(placement: .principal) {
                    Picker("Section", selection: $segment) {
                        ForEach(Segment.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 220)
                }
            }
            .navigationDestination(for: LiftSummaryDTO.self) { LiftDetailView(lift: $0) }
            .navigationDestination(for: InsightsRoute.self) { _ in InsightsView() }
            .navigationDestination(for: RunRoute.self) { RunAnalysisView(runId: $0.id) }
            .navigationDestination(item: $openRun) { RunAnalysisView(runId: $0.id) }
        }
    }
}

struct RunRoute: Hashable, Identifiable {
    let id: String
}

/// "Oct 24" for a `YYYY-MM-DD` string.
enum DayLabel {
    static func short(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).day())
    }
    static func medium(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).day().year())
    }
    static func monthYear(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).year())
    }
}
