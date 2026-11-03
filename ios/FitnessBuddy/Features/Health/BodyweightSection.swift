import FitnessCore
import SwiftData
import SwiftUI

/// "Weigh-Ins": your own log entries (swipe to delete; a tombstone that syncs) or a prompt to log the first one.
/// Logging itself is the trailing "Log Weight" button of the section, in HealthTabView.
struct WeighInsSection: View {
    let measurements: [LocalBodyMeasurement]
    /// Any weight at all (yours or from the scale); only changes the empty-state sentence.
    let hasAnyWeight: Bool
    let logWeight: () -> Void

    var body: some View {
        let mine = Array(measurements.filter { $0.deletedAt == nil }.prefix(6))
        PageSection("Weigh-Ins", info: "Weights you log are saved on this device first and synced when a connection is available. Weights from your scale show in the bodyweight chart but cannot be deleted here.") {
            if mine.isEmpty {
                Card {
                    Text(hasAnyWeight ? "Weights you log appear here. Swipe an entry to delete it."
                                      : "No weigh-ins yet. Log your weight to start the curve.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            } else {
                MyEntriesCard(measurements: mine)
            }
        } trailing: {
            Button(action: logWeight) { Label("Log Weight", systemImage: "plus") }
                .font(.subheadline.weight(.semibold))
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .tint(Domain.body.color)
        }
    }
}

private struct MyEntriesCard: View {
    @Environment(AppEnvironment.self) private var env
    @ScaledMetric(relativeTo: .body) private var rowHeight: CGFloat = 56
    let measurements: [LocalBodyMeasurement]

    var body: some View {
        Card {
            CardHeader(title: "Your Entries", symbol: "list.bullet", color: Domain.body.color, info: "Swipe left to delete.")
            List {
                ForEach(measurements, id: \.id) { m in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            MetricText(value: Fmt.number(m.weightKg, maxFraction: 1), unit: "kg", size: .headline)
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
            .frame(height: CGFloat(measurements.count) * rowHeight + 4)
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
    @State private var day = AppClock.instant
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
                    DatePicker("Date", selection: $day, in: ...AppClock.instant, displayedComponents: .date)
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
                #if DEBUG
                // The UI test pass (-uiReset) keeps the keyboard down so it never covers the screenshot.
                if UserDefaults.standard.bool(forKey: "uiReset") { return }
                #endif
                focused = true
            }
        }
        .screenIdentifier(AXID.Health.logWeight)
        .tint(Domain.body.color)
        .presentationDetents([.medium])
    }

    private func save() {
        guard let value, isValid else { return }
        BodyweightLog.add(weightKg: value, day: day, in: env.context)
        env.sync.request(.bodyLogged)
        dismiss()
    }
}
