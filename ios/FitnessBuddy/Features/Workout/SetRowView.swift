import SwiftUI

struct SetRowView: View {
    @Bindable var set: LocalSet
    let label: String
    let ladder: [Double]
    let equipment: String
    let dumbbellIncrement: Double?
    var increments: [String: Double]? = nil
    let service: WorkoutService
    let session: LocalSession
    let onToggle: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                typeBadge
                StepperField(title: "Weight", unit: "kg", value: set.weightKg, fractionDigits: 2,
                             decrement: { service.setWeight(set, WeightStepper.previous(from: set.weightKg, ladder: ladder, equipmentType: equipment, dumbbellIncrement: dumbbellIncrement, increments: increments), in: session) },
                             increment: { service.setWeight(set, WeightStepper.next(from: set.weightKg, ladder: ladder, equipmentType: equipment, dumbbellIncrement: dumbbellIncrement, increments: increments), in: session) },
                             commit: { service.setWeight(set, $0, in: session) })
                if set.isTimed {
                    StepperField(title: "Seconds", unit: "sec", value: set.durationS, fractionDigits: 0,
                                 decrement: { service.setDuration(set, CountStepper.seconds(set.durationS, delta: -5)) },
                                 increment: { service.setDuration(set, CountStepper.seconds(set.durationS, delta: 5)) },
                                 commit: { service.setDuration(set, $0) })
                } else {
                    StepperField(title: "Reps", unit: "reps", value: set.reps.map(Double.init), fractionDigits: 0,
                                 decrement: { service.setReps(set, CountStepper.reps(set.reps, delta: -1)) },
                                 increment: { service.setReps(set, CountStepper.reps(set.reps, delta: 1)) },
                                 commit: { service.setReps(set, $0.map { Int($0.rounded()) }) })
                }
                checkButton
            }
            if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 32)
            }
        }
        .opacity(set.isCompleted ? 0.9 : 1)
    }

    private var hint: String? {
        var parts: [String] = []
        if let t = fmt(weight: set.targetWeightKg, reps: set.targetReps, duration: set.targetDurationS) { parts.append("Target \(t)") }
        if let p = fmt(weight: set.prevWeightKg, reps: set.prevReps, duration: set.prevDurationS) { parts.append("Last \(p)") }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }

    private func fmt(weight: Double?, reps: Int?, duration: Double?) -> String? {
        if let duration { return "\(Fmt.number(duration, maxFraction: 0)) s" }
        guard weight != nil || reps != nil else { return nil }
        return "\(Fmt.weight(weight)) × \(reps.map(String.init) ?? "–")"
    }

    private var typeBadge: some View {
        Menu {
            ForEach([("working", "Working set"), ("warmup", "Warm-up"), ("drop", "Drop set"), ("failure", "To failure")], id: \.0) { type, title in
                Button(title) { service.setType(set, type) }
            }
        } label: {
            Text(label)
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(set.setType == "working" ? Color.secondary : Domain.lifting.color)
                .frame(width: 26, height: 44)
        }
        .accessibilityLabel("Set \(label), change type")
    }

    private var checkButton: some View {
        Button {
            onToggle(service.toggleComplete(set))
        } label: {
            Image(systemName: set.isCompleted ? "checkmark.circle.fill" : "circle")
                .font(.system(.largeTitle))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(set.isCompleted ? Color.green : Color.secondary)
                .frame(width: 46, height: 46)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .sensoryFeedback(trigger: set.completedAt) { _, new in new != nil ? .impact(weight: .medium) : nil }
        .accessibilityLabel(set.isCompleted ? "Set \(label) completed" : "Complete set \(label)")
        .accessibilityHint("Logs the shown weight and reps")
    }
}

/// [-]  value  [+] with direct number entry in the middle.
struct StepperField: View {
    let title: String
    let unit: String
    let value: Double?
    let fractionDigits: Int
    let decrement: () -> Void
    let increment: () -> Void
    let commit: (Double?) -> Void

    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 0) {
            stepButton("minus", "Decrease \(title.lowercased())", decrement)
            VStack(spacing: 0) {
                TextField("–", text: $text)
                    .focused($focused)
                    .keyboardType(fractionDigits > 0 ? .decimalPad : .numberPad)
                    .multilineTextAlignment(.center)
                    .metricStyle(.title3, weight: .semibold)
                    .minimumScaleFactor(0.6)
                    .accessibilityLabel(title)
                Text(unit).font(.caption2).foregroundStyle(.secondary)
            }
            .frame(minWidth: 40)
            stepButton("plus", "Increase \(title.lowercased())", increment)
        }
        .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onAppear { text = format(value) }
        .onChange(of: value) { _, new in if !focused { text = format(new) } }
        .onChange(of: focused) { _, isFocused in
            if !isFocused { commitText() }
        }
    }

    private func stepButton(_ symbol: String, _ label: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.footnote.weight(.bold))
                .frame(width: 30, height: 48)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Domain.lifting.color)
        .accessibilityLabel(label)
    }

    private func commitText() {
        let cleaned = text.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
        if cleaned.isEmpty { commit(nil) } else if let v = Double(cleaned) { commit(v) }
        text = format(value)
    }

    private func format(_ v: Double?) -> String {
        guard let v else { return "" }
        return Fmt.number(v, maxFraction: fractionDigits)
    }
}
