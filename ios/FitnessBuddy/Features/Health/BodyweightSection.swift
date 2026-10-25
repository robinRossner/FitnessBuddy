import Charts
import SwiftData
import SwiftUI

struct BodyweightCard: View {
    @Environment(AppEnvironment.self) private var env
    let server: [BodyweightPointDTO]
    let summary: HealthSummaryDTO?
    let measurements: [LocalBodyMeasurement]
    let range: HealthRange
    let logWeight: () -> Void

    private var entries: [BodyweightEntry] {
        BodyweightMerge.merge(server: server, local: measurements.map(BodyweightLog.snapshot))
    }

    var body: some View {
        let all = entries
        let shown = BodyweightMerge.within(all, days: range.rawValue)
        let latest = all.last
        let change = BodyweightMerge.change4wPct(all) ?? summary?.bodyweightChange4wPct
        let mine = measurements.filter { $0.deletedAt == nil }
        VStack(spacing: 16) {
            ChartCard(title: "Bodyweight", symbol: "scalemass.fill", color: ChartPalette.weight, trailing: {
                Button(action: logWeight) { Label("Log weight", systemImage: "plus") }
                    .font(.subheadline.weight(.semibold))
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .tint(ChartPalette.weight)
            }) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(latest.map { Fmt.number($0.weightKg, maxFraction: 1) } ?? "–").metricStyle(.largeTitle)
                    Text("kg").font(.headline).foregroundStyle(.secondary)
                    Spacer()
                    if let change {
                        Chip(text: "\(Fmt.percent(change)) in 4 weeks", color: .secondary)
                    }
                }
                if shown.count < 2 {
                    ChartEmpty(symbol: "scalemass", message: shown.isEmpty ? "No weigh-ins yet. Log your weight to start the curve." : "One weigh-in so far. Log another to see the trend.", height: 110)
                } else {
                    Chart {
                        ForEach(shown) { e in
                            LineMark(x: .value("Date", e.date), y: .value("kg", e.weightKg))
                                .interpolationMethod(.monotone)
                                .foregroundStyle(ChartPalette.weight)
                                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                            PointMark(x: .value("Date", e.date), y: .value("kg", e.weightKg))
                                .symbolSize(e.isPending ? 80 : 36)
                                .foregroundStyle(e.isPending ? Color.orange : ChartPalette.weight)
                        }
                    }
                    .chartXAxis { AxisMarks(values: .automatic(desiredCount: 4)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.month(.abbreviated).day()) } }
                    .chartYScale(domain: .automatic(includesZero: false))
                    .frame(height: 150)
                    .accessibilityLabel("Bodyweight over \(range.rawValue) days")
                    .accessibilityValue("Latest \(Fmt.number(latest?.weightKg ?? 0, maxFraction: 1)) kilograms" + (shown.contains { $0.isPending } ? ", includes entries waiting to sync" : ""))
                    if shown.contains(where: \.isPending) {
                        ChartNote(text: "Orange points are waiting to sync.", symbol: "icloud.and.arrow.up")
                    }
                }
            }
            if !mine.isEmpty { MyEntriesCard(measurements: Array(mine.prefix(6))) }
        }
    }
}

/// Your own log entries; swipe to delete (a tombstone that syncs).
private struct MyEntriesCard: View {
    @Environment(AppEnvironment.self) private var env
    let measurements: [LocalBodyMeasurement]

    var body: some View {
        ChartCard(title: "Your entries", subtitle: "Swipe left to delete.", symbol: "list.bullet", color: ChartPalette.weight) {
            List {
                ForEach(measurements, id: \.id) { m in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(Fmt.number(m.weightKg, maxFraction: 1)) kg").metricStyle(.headline)
                            Text(m.measuredAt.formatted(.dateTime.month(.abbreviated).day().year())).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if m.dirty && m.rejectedReason == nil {
                            Label("Waiting to sync", systemImage: "icloud.and.arrow.up").font(.caption).foregroundStyle(.orange)
                                .labelStyle(.titleAndIcon)
                        } else if m.rejectedReason != nil {
                            Label("Rejected", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.red)
                        }
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 6, leading: 0, bottom: 6, trailing: 0))
                    .accessibilityElement(children: .combine)
                    .accessibilityAction(named: "Delete") { delete(m) }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button(role: .destructive) { delete(m) } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
            .listStyle(.plain)
            .scrollDisabled(true)
            .scrollContentBackground(.hidden)
            .frame(height: CGFloat(measurements.count) * 56 + 4)
        }
    }

    private func delete(_ m: LocalBodyMeasurement) {
        BodyweightLog.delete(m, in: env.context)
        env.sync.request(.bodyLogged)
    }
}

// MARK: - Log sheet

struct LogWeightSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    let lastWeight: Double?

    @State private var text = ""
    @State private var day = Date()
    @FocusState private var focused: Bool

    private var value: Double? { BodyweightLog.parse(text) }
    private var isValid: Bool { value.map(BodyweightLog.isValid) ?? false }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        TextField("Weight", text: $text)
                            .keyboardType(.decimalPad)
                            .font(.metric(.largeTitle))
                            .focused($focused)
                            .accessibilityLabel("Weight in kilograms")
                        Text("kg").font(.title3).foregroundStyle(.secondary)
                    }
                    DatePicker("Date", selection: $day, in: ...Date(), displayedComponents: .date)
                } footer: {
                    Text(text.isEmpty || isValid ? "Saved on this device first and synced when a connection is available."
                                                   : "Enter a weight between \(Int(BodyweightLog.validRange.lowerBound)) and \(Int(BodyweightLog.validRange.upperBound)) kg.")
                        .foregroundStyle(text.isEmpty || isValid ? Color.secondary : Color.red)
                }
            }
            .navigationTitle("Log weight")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Save", action: save).disabled(!isValid) }
            }
            .onAppear {
                if text.isEmpty, let lastWeight { text = Fmt.number(lastWeight, maxFraction: 1) }
                focused = true
            }
        }
        .presentationDetents([.medium])
    }

    private func save() {
        guard let value, isValid else { return }
        BodyweightLog.add(weightKg: value, day: day, in: env.context)
        env.sync.request(.bodyLogged)
        dismiss()
    }
}
