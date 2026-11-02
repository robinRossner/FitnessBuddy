import FitnessCore
import SwiftData
import SwiftUI

/// Health tab: readiness against your baselines, then one card per signal (HRV, resting HR, sleep, steps, bodyweight)
/// driven by a 7 / 30 / 90 day picker, then the weigh-ins you logged.
struct HealthTabView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \LocalBodyMeasurement.measuredAt, order: .reverse) private var measurements: [LocalBodyMeasurement]
    @State private var span: HealthSignals.Span = .month
    @State private var showWeightSheet = false

    private let openSheet: String?

    /// `-uiSheet weight|readiness` opens that sheet at launch (screenshots, DEBUG only).
    init() {
        #if DEBUG
        let d = UserDefaults.standard.string(forKey: "uiSheet")
        openSheet = d
        _showWeightSheet = State(initialValue: d == "weight")
        #else
        openSheet = nil
        #endif
    }

    private var health: StatsResource<HealthStatsDTO> { env.stats.resource(StatsEndpoints.health()) }

    var body: some View {
        let health = health
        let sources = MetricCatalog.Sources(health: health.value, volume: nil,
                                            localWeights: measurements.map(BodyweightLog.snapshot))
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Kit.groupSpacing) {
                    LoadableContent(health.value, else: health.loadState("health data")) { data in
                        ReadinessBreakdownCard(summary: readiness(data), time: readinessTime(data),
                                               startOpen: openSheet == "readiness")
                    }
                    signals(health.value, sources: sources)
                    WeighInsSection(measurements: measurements, hasAnyWeight: !sources.bodyweight.isEmpty,
                                    logWeight: { showWeightSheet = true })
                    FreshnessLabel(resource: health)
                }
                .pagePadding()
            }
            .screenStyle()
            .navigationTitle("Health")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    HStack(spacing: 12) { SyncStatusBadge(); GarminSyncButton(resources: [health]) }
                }
            }
            .refreshable { await refreshStats(env, [health]) }
            .loadsStats([health])
            .garminSyncBanner()
            .sheet(isPresented: $showWeightSheet) {
                LogWeightSheet(lastWeight: lastWeight)
            }
        }
    }

    // MARK: Readiness

    private func readiness(_ data: HealthStatsDTO) -> ReadinessSummary? {
        ReadinessSummary.make(today: data.readinessToday, legacy: nil, day: HealthTransforms.latestReadinessDay(data.days))
    }

    private func readinessTime(_ data: HealthStatsDTO) -> String? {
        if data.readinessToday != nil { return "Today" }
        guard let day = HealthTransforms.latestReadinessDay(data.days) else { return nil }
        return MetricSeries.timeLabel(MetricPoint(day: String(day.date.prefix(10)), value: 0), metric: MetricCatalog.hrv)
    }

    // MARK: Signals

    @ViewBuilder
    private func signals(_ data: HealthStatsDTO?, sources: MetricCatalog.Sources) -> some View {
        let summary = data?.summary
        PageSection("Signals", info: "The picker sets the period of every chart and average below. The dashed line is the average of that period. Tap a card for the full history.") {
            Picker("Range", selection: $span.animation()) {
                ForEach(HealthSignals.Span.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.bottom, 2)

            if data != nil {
                ForEach([MetricCatalog.hrv, MetricCatalog.restingHR, MetricCatalog.sleep]) { metric in
                    let points = MetricCatalog.points(metric, from: sources)
                    SignalCard(metric: metric, points: points, span: span,
                               footnote: baselineFootnote(metric, points: points, summary: summary),
                               emptyMessage: "Needs a few days of watch data.")
                }
                let steps = MetricCatalog.points(MetricCatalog.steps, from: sources)
                if !steps.isEmpty {
                    SignalCard(metric: MetricCatalog.steps, points: steps, span: span,
                               footnote: stepsFootnote(data: data, summary: summary))
                }
            }
            SignalCard(metric: MetricCatalog.bodyweight, points: MetricCatalog.points(MetricCatalog.bodyweight, from: sources),
                       span: span, footnote: weightFootnote(sources.bodyweight, summary: summary),
                       emptyMessage: "No weigh-ins in this period. Log your weight to start the curve.")
        }
    }

    /// "vs 28-day average": the period average against the baseline the readiness uses.
    private func baselineFootnote(_ metric: MetricInfo, points: [MetricPoint], summary: HealthSummaryDTO?) -> SignalFootnote? {
        let window = HealthSignals.window(points, days: span.rawValue)
        guard let delta = HealthSignals.deltaPct(HealthSignals.average(window), baseline: MetricCatalog.baseline(metric, summary: summary))
        else { return nil }
        return SignalFootnote(text: "vs 28-day average", delta: delta, higherIsBetter: metric.higherIsBetter ?? true)
    }

    private func stepsFootnote(data: HealthStatsDTO?, summary: HealthSummaryDTO?) -> SignalFootnote? {
        guard let data else { return nil }
        let n = HealthTransforms.daysAtGoal(data.days, summary: summary)
        return SignalFootnote(text: "\(n) of the last 7 days above 10,000")
    }

    private func weightFootnote(_ entries: [BodyweightEntry], summary: HealthSummaryDTO?) -> SignalFootnote? {
        guard let change = BodyweightMerge.change4wPct(entries) ?? summary?.bodyweightChange4wPct else { return nil }
        return SignalFootnote(text: "\(Fmt.percent(change)) in 4 weeks")
    }

    private var lastWeight: Double? {
        let live = measurements.filter { $0.deletedAt == nil }
        return live.first?.weightKg ?? health.value?.bodyweight.last?.weightKg
    }
}
