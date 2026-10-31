import SwiftUI

struct SetRowView: View {
    @Bindable var set: LocalSet
    let label: String
    let ladder: [Double]
    let equipment: String
    let dumbbellIncrement: Double?
    var increments: [String: Double]? = nil
    /// The exercise's own load step (Edit exercise), replaces ladder and equipment default.
    var step: Double? = nil
    let service: WorkoutService
    let session: LocalSession
    let onToggle: (Bool) -> Void
    /// Fields the last tick attempt found empty (shown in red until filled in).
    @State private var missing: Set<SetValidation.Field> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                typeBadge
                StepperField(title: "Weight", unit: "kg", value: set.weightKg, fractionDigits: 2,
                             placeholder: set.weightKg == nil ? (SetupRules.placeholder(hintWeightKg: set.hintWeightKg) ?? "–") : "–",
                             highlighted: missing.contains(.weight),
                             decrement: { service.setWeight(set, WeightStepper.previous(from: set.weightKg, ladder: ladder, equipmentType: equipment, dumbbellIncrement: dumbbellIncrement, increments: increments, step: step), in: session) },
                             increment: {
                                 // an empty baseline set adopts its hint on the first tap
                                 if set.weightKg == nil, let hint = set.hintWeightKg { service.setWeight(set, hint, in: session); return }
                                 service.setWeight(set, WeightStepper.next(from: set.weightKg, ladder: ladder, equipmentType: equipment, dumbbellIncrement: dumbbellIncrement, increments: increments, step: step), in: session)
                             },
                             commit: { service.setWeight(set, $0, in: session) })
                if set.isTimed {
                    StepperField(title: "Seconds", unit: "sec", value: set.durationS, fractionDigits: 0,
                                 highlighted: missing.contains(.duration),
                                 decrement: { service.setDuration(set, CountStepper.seconds(set.durationS, delta: -5)) },
                                 increment: { service.setDuration(set, CountStepper.seconds(set.durationS, delta: 5)) },
                                 commit: { service.setDuration(set, $0) })
                } else {
                    StepperField(title: "Reps", unit: "reps", value: set.reps.map(Double.init), fractionDigits: 0,
                                 highlighted: missing.contains(.reps),
                                 decrement: { service.setReps(set, CountStepper.reps(set.reps, delta: -1)) },
                                 increment: { service.setReps(set, CountStepper.reps(set.reps, delta: 1)) },
                                 commit: { service.setReps(set, $0.map { Int($0.rounded()) }) })
                }
                checkButton
            }
            if !missing.isEmpty {
                Text(missingText)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.red)
                    .padding(.leading, 32)
            } else if let hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 32)
            }
        }
        .opacity(set.isCompleted ? 0.9 : 1)
        .onChange(of: set.weightKg) { _, _ in recheck() }
        .onChange(of: set.reps) { _, _ in recheck() }
        .onChange(of: set.durationS) { _, _ in recheck() }
        .sensoryFeedback(.warning, trigger: missing) { _, new in !new.isEmpty }
    }

    private var missingText: String {
        let what = [missing.contains(.weight) ? "weight" : nil, missing.contains(.reps) ? "reps" : nil,
                    missing.contains(.duration) ? "seconds" : nil].compactMap { $0 }
        return "Enter " + ListFormatter.localizedString(byJoining: what) + " to log this set"
    }

    /// Once the user fills a field in, drop it from the red list.
    private func recheck() {
        guard !missing.isEmpty else { return }
        missing = missing.intersection(service.missingFields(set))
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
            let wasCompleted = set.isCompleted
            let done = service.toggleComplete(set)
            missing = (done || wasCompleted) ? [] : service.missingFields(set)
            onToggle(done)
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
    var placeholder = "–"
    /// Red outline: the value is needed before the set can be logged.
    var highlighted = false
    let decrement: () -> Void
    let increment: () -> Void
    let commit: (Double?) -> Void

    @State private var text = ""
    @FocusState private var focused: Bool
    /// Set by +/-: the typed text was flushed already, so losing focus must not re-commit the stale buffer.
    @State private var skipBlurCommit = false

    var body: some View {
        HStack(spacing: 0) {
            stepButton("minus", "Decrease \(title.lowercased())", decrement)
            VStack(spacing: 0) {
                TextField(placeholder, text: $text)
                    .focused($focused)
                    .keyboardType(fractionDigits > 0 ? .decimalPad : .numberPad)
                    .multilineTextAlignment(.center)
                    .metricStyle(.title3, weight: .semibold)
                    .minimumScaleFactor(0.6)
                    .accessibilityLabel(title)
                Text(unit).font(.caption2).foregroundStyle(.secondary)
            }
            .frame(minWidth: 40, maxHeight: .infinity)
            .contentShape(Rectangle())
            .onTapGesture { focused = true }
            stepButton("plus", "Increase \(title.lowercased())", increment)
        }
        .frame(height: 48)
        .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            if highlighted {
                RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.red, lineWidth: 2)
            }
        }
        .onAppear { text = format(value) }
        // external changes (steppers, service) refresh the buffer; typing that already matches is left alone
        .onChange(of: value) { _, new in
            if Double(Self.clean(text)) != new, !(new == nil && text.isEmpty) { text = format(new) }
        }
        .onChange(of: focused) { _, isFocused in
            if !isFocused {
                if skipBlurCommit { skipBlurCommit = false; text = format(value) } else { commitText() }
            }
        }
        // typed numbers count at once, so ticking the set while the keyboard is still up logs what was typed
        .onChange(of: text) { _, new in
            if focused, let v = Double(Self.clean(new)) { commit(v) }
        }
    }

    private static func clean(_ text: String) -> String {
        text.replacingOccurrences(of: ",", with: ".").trimmingCharacters(in: .whitespaces)
    }

    private func stepButton(_ symbol: String, _ label: String, _ action: @escaping () -> Void) -> some View {
        Button {
            // flush what was typed, end editing, then step the committed value in the same tap
            let wasFocused = focused
            let cleaned = Self.clean(text)
            if wasFocused {
                if cleaned.isEmpty { commit(nil) } else if let v = Double(cleaned) { commit(v) }
                skipBlurCommit = true
                focused = false
            }
            action()
            if !wasFocused { text = format(value) }
        } label: {
            Image(systemName: symbol)
                .font(.footnote.weight(.bold))
                .frame(width: 34)
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Domain.lifting.color)
        .accessibilityLabel(label)
    }

    private func commitText() {
        let cleaned = Self.clean(text)
        if cleaned.isEmpty { commit(nil) } else if let v = Double(cleaned) { commit(v) }
        text = format(value)
    }

    private func format(_ v: Double?) -> String {
        guard let v else { return "" }
        return Fmt.number(v, maxFraction: fractionDigits)
    }
}
