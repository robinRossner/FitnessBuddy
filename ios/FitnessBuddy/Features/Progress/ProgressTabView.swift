import FitnessCore
import SwiftUI

/// Progress tab: Lifting | Running | Insights under one large title, each a page of the design kit (DesignKit.swift).
/// Data comes from `StatsRepository` (cached, offline-safe) and the cached plan.
struct ProgressTabView: View {
    enum Segment: String, CaseIterable, Identifiable {
        case lifting = "Lifting", running = "Running", insights = "Insights"
        var id: String { rawValue }
    }

    @Environment(AppEnvironment.self) private var env
    @State private var segment: Segment = .lifting
    @State private var path = NavigationPath()

    #if DEBUG
    /// Launch arguments for screenshots / UI runs: `-uiSegment running|insights`, `-uiLift <variant id>`, `-uiRun <run id>`,
    /// `-uiSheet insights|vdot|efficiency`.
    init() {
        let d = UserDefaults.standard
        _segment = State(initialValue: Segment.allCases.first { $0.rawValue.lowercased() == d.string(forKey: "uiSegment") } ?? .lifting)
        var p = NavigationPath()
        if let id = d.string(forKey: "uiLift").flatMap(Int.init) { p.append(LiftSummaryDTO(variantId: id, name: "Lift")) }
        if let id = d.string(forKey: "uiRun") { p.append(RunRoute(id: id)) }
        switch d.string(forKey: "uiSheet") {
        case "insights": p.append(InsightsRoute())
        case "vdot": p.append(RunningRoute.vdot)
        case "efficiency": p.append(RunningRoute.efficiency)
        default: break
        }
        _path = State(initialValue: p)
    }
    #endif

    /// What pull-to-refresh refetches: the shown segment's stats.
    private var resources: [any StatsLoadable] {
        switch segment {
        case .lifting: [env.stats.resource(StatsEndpoints.lifts), env.stats.resource(StatsEndpoints.weeklyVolume())]
        case .running: [env.stats.resource(StatsEndpoints.running()), env.stats.resource(StatsEndpoints.recentRuns())]
        case .insights: [env.stats.resource(StatsEndpoints.correlations())]
        }
    }

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                    Picker("Section", selection: $segment) {
                        ForEach(Segment.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    switch segment {
                    case .lifting: LiftingProgressView()
                    case .running: RunningProgressView()
                    case .insights: InsightsContent()
                    }
                }
                .pagePadding()
            }
            .screenStyle()
            .navigationTitle("Progress")
            .refreshable { await refreshStats(env, resources) }
            .navigationDestination(for: LiftSummaryDTO.self) { LiftDetailView(lift: $0) }
            .navigationDestination(for: InsightsRoute.self) { _ in InsightsView() }
            .navigationDestination(for: RunRoute.self) { RunAnalysisView(runId: $0.id) }
            .navigationDestination(for: RunningRoute.self) { RunningRouteView(route: $0) }
        }
    }
}

struct RunRoute: Hashable, Identifiable {
    let id: String
}
