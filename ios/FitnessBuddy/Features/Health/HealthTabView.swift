import SwiftData
import SwiftUI

struct HealthTabView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \LocalBodyMeasurement.measuredAt, order: .reverse) private var measurements: [LocalBodyMeasurement]
    @State private var range: HealthRange = .thirty
    @State private var showWeightSheet = false

    #if DEBUG
    /// `-uiSheet weight` opens the log sheet at launch (screenshots).
    init() { _showWeightSheet = State(initialValue: UserDefaults.standard.string(forKey: "uiSheet") == "weight") }
    #endif

    private var health: StatsResource<HealthStatsDTO> { env.stats.resource(StatsEndpoints.health()) }
    private var correlations: StatsResource<CorrelationsDTO> { env.stats.resource(StatsEndpoints.correlations()) }

    var body: some View {
        let health = health, correlations = correlations
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if let data = health.value {
                        let days = HealthTransforms.window(data.days, range: range)
                        HealthHeader(days: data.days, summary: data.summary)
                        Picker("Range", selection: $range) {
                            ForEach(HealthRange.allCases) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        HRVCard(days: days)
                        RHRCard(days: days)
                        SleepCard(days: days)
                        BodyBatteryCard(days: days)
                        StepsCard(days: days)
                        ReadinessCalendarCard(days: data.days, count: range.rawValue)
                        BodyweightCard(server: data.bodyweight, summary: data.summary, measurements: measurements,
                                       range: range, logWeight: { showWeightSheet = true })
                    } else {
                        StatsPlaceholder(resource: health, symbol: "heart.text.square.fill", emptyTitle: "health data")
                        BodyweightCard(server: [], summary: nil, measurements: measurements, range: range,
                                       logWeight: { showWeightSheet = true })
                    }
                    CorrelationsSection(resource: correlations)
                    FreshnessLabel(resource: health)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Color.screenBackground)
            .navigationTitle("Health")
            .toolbar { ToolbarItem(placement: .topBarLeading) { SyncStatusBadge() } }
            .refreshable { await refreshStats(env, [health, correlations]) }
            .loadsStats([health, correlations])
            .sheet(isPresented: $showWeightSheet) {
                LogWeightSheet(lastWeight: lastWeight)
            }
        }
    }

    private var lastWeight: Double? {
        let live = measurements.filter { $0.deletedAt == nil }
        return live.first?.weightKg ?? health.value?.bodyweight.last?.weightKg
    }
}

// MARK: - Header: readiness + 7d vs 28d

private struct HealthHeader: View {
    let days: [HealthDayDTO]
    let summary: HealthSummaryDTO?

    private var readiness: ReadinessDTO? {
        guard let day = HealthTransforms.latestReadinessDay(days), let r = day.readiness else { return nil }
        let level = r.readinessLevel
        return ReadinessDTO(date: day.date, color: level == .unknown ? nil : level.rawValue, known: level != .unknown, reason: r.reason)
    }

    var body: some View {
        VStack(spacing: 16) {
            ReadinessCard(readiness: readiness)
            Card {
                SectionLabel(title: "7 days vs 28 days", symbol: "heart.fill", color: Domain.recovery.color)
                HStack(alignment: .top, spacing: 12) {
                    CompareTile(title: "HRV", short: summary?.hrv7d, long: summary?.hrv28d, unit: "ms", higherIsBetter: true,
                                format: { Fmt.number($0, maxFraction: 0) })
                    CompareTile(title: "Resting HR", short: summary?.rhr7d, long: summary?.rhr28d, unit: "bpm", higherIsBetter: false,
                                format: { Fmt.number($0, maxFraction: 0) })
                    CompareTile(title: "Sleep", short: summary?.sleep7dS, long: summary?.sleep28dS, unit: "h", higherIsBetter: true,
                                format: { Fmt.hours($0) }, deltaOnRaw: true)
                }
            }
        }
    }
}

private struct CompareTile: View {
    let title: String
    let short: Double?
    let long: Double?
    let unit: String
    let higherIsBetter: Bool
    let format: (Double) -> String
    var deltaOnRaw = false

    var body: some View {
        let delta = HealthTransforms.deltaPct(short: short, long: long)
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(short.map(format) ?? "–").metricStyle(.title2)
                Text(unit).font(.footnote.weight(.medium)).foregroundStyle(.secondary)
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            Text("28d: \(long.map(format) ?? "–")").font(.caption2).foregroundStyle(.secondary)
            if let delta {
                HStack(spacing: 2) {
                    Image(systemName: delta > 1 ? "arrow.up.right" : (delta < -1 ? "arrow.down.right" : "arrow.right"))
                    Text(Fmt.percent(delta))
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(color(delta))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title): seven day average \(short.map(format) ?? "unknown") \(unit), twenty-eight day average \(long.map(format) ?? "unknown"), \(delta.map { Fmt.percent($0) } ?? "no change figure")")
    }

    private func color(_ delta: Double) -> Color {
        if abs(delta) <= 1 { return .secondary }
        return (delta > 0) == higherIsBetter ? .green : .orange
    }
}
