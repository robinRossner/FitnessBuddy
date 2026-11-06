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
    /// Launch arguments for screenshots / UI runs, honoured only with `-uiTab progress`: `-uiSegment running|insights`,
    /// `-uiLift <variant id>`, `-uiRun <run id>`, `-uiRoute insights|vdot|efficiency|allruns|insight` pushes a screen
    /// (the first three also as `-uiSheet`, their old spelling), `-uiSheet alllifts|rundetail|rundetail-easy` opens a sheet.
    init() {
        let d = UserDefaults.standard
        guard ProgressLaunch.active else { return }
        _segment = State(initialValue: Segment.allCases.first { $0.rawValue.lowercased() == d.string(forKey: "uiSegment") } ?? .lifting)
        var p = NavigationPath()
        if let id = d.string(forKey: "uiLift").flatMap(Int.init) { p.append(LiftSummaryDTO(variantId: id, name: "Lift")) }
        if let id = d.string(forKey: "uiRun") { p.append(RunRoute(id: id)) }
        switch d.string(forKey: "uiRoute") ?? d.string(forKey: "uiSheet") {
        case "insights": p.append(InsightsRoute())
        case "vdot": p.append(RunningRoute.vdot)
        case "efficiency": p.append(RunningRoute.efficiency)
        case "allruns": p.append(RunningRoute.allRuns)
        case "insight": p.append(InsightDetailRoute(id: "sleep_hrv"))
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
            .navigationDestination(for: InsightDetailRoute.self) { InsightDetailRouteView(id: $0.id) }
        }
        .screenIdentifier(AXID.Screen.progress)
        .persistentSystemOverlays(Self.systemOverlays)
    }

    /// The home indicator fades out after a few idle seconds, which made screenshots differ by a few pixels; the screenshot
    /// pass (`-uiNoAnimations`) hides it from the start.
    private static var systemOverlays: Visibility {
        #if DEBUG
        UITestSupport.animationsDisabled ? .hidden : .automatic
        #else
        .automatic
        #endif
    }
}

struct RunRoute: Hashable, Identifiable {
    let id: String
}

#if DEBUG
/// The launch arguments of this tab count only when `-uiTab` names it, so tabs never react to each other's values.
enum ProgressLaunch {
    static var active: Bool { UserDefaults.standard.string(forKey: "uiTab") == "progress" }

    /// The `-uiSheet` value when it is meant for this tab.
    static var sheet: String? { active ? UserDefaults.standard.string(forKey: "uiSheet") : nil }
}
#endif
