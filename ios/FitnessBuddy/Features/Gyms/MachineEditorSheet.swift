import FitnessCore
import SwiftUI

/// One weight step as a chip; tap removes it (when `onRemove` is set).
struct WeightChip: View {
    let kg: Double
    var onRemove: (() -> Void)? = nil
    var muted = false

    var body: some View {
        let label = Text(Fmt.number(kg)).font(.subheadline.weight(.semibold).monospacedDigit())
        if let onRemove {
            Button(action: onRemove) {
                HStack(spacing: 4) {
                    label
                    Image(systemName: "xmark.circle.fill").font(.caption).foregroundStyle(.secondary)
                }
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Domain.lifting.color.opacity(0.15), in: Capsule())
                .foregroundStyle(.primary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(Fmt.number(kg)) kilograms, remove")
        } else {
            label
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(Color.secondary.opacity(muted ? 0.12 : 0.2), in: Capsule())
                .foregroundStyle(muted ? .secondary : .primary)
        }
    }
}

/// Set up (or change) the machine of one exercise at one gym: name, equipment, notes and the weight stack's steps.
struct MachineEditorSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss

    let gymId: Int
    let gymName: String
    let item: MachineItemDTO
    var onSaved: (MachineItemDTO?) -> Void = { _ in }

    @State private var name: String
    @State private var equipmentType: String
    @State private var notes: String
    @State private var ladder: [Double]
    @State private var startText: String
    @State private var stepText: String
    @State private var count: Int
    @State private var preview: [Double]
    @State private var manualText = ""
    @State private var saving = false
    @State private var errorText: String?
    @FocusState private var focus: Field?

    private enum Field { case name, start, step, manual, notes }

    init(gymId: Int, gymName: String, item: MachineItemDTO, onSaved: @escaping (MachineItemDTO?) -> Void = { _ in }) {
        self.gymId = gymId
        self.gymName = gymName
        self.item = item
        self.onSaved = onSaved
        let ladder = item.variant?.ladder ?? []
        let seed = LadderRules.generatorSeed(for: ladder)
        _name = State(initialValue: item.variant?.name ?? MachineLogic.defaultName(exercise: item.exerciseName, gym: gymName))
        _equipmentType = State(initialValue: item.variant?.equipmentType ?? item.equipmentType ?? "machine_stack")
        _notes = State(initialValue: item.variant?.notes ?? "")
        _ladder = State(initialValue: ladder)
        _startText = State(initialValue: Fmt.number(seed.start))
        _stepText = State(initialValue: Fmt.number(seed.step))
        _count = State(initialValue: seed.count)
        _preview = State(initialValue: LadderRules.generate(start: seed.start, step: seed.step, count: seed.count))
    }

    private var draft: MachineDraft { MachineDraft(name: name, equipmentType: equipmentType, notes: notes, ladder: ladder) }
    private var canWrite: Bool { env.gyms.canWrite }
    private var canSave: Bool { draft.isValid && canWrite && !saving }

    private var start: Double { LadderRules.parse(startText) ?? 0 }
    private var step: Double { LadderRules.parse(stepText) ?? 0 }

    private struct GeneratorKey: Equatable { var start: Double; var step: Double; var count: Int }

    private var typeChoices: [(String, String)] {
        var base = [("machine_stack", "Weight stack"), ("cable", "Cable"), ("smith", "Smith machine"), ("plate_loaded", "Plate-loaded")]
        if !base.contains(where: { $0.0 == equipmentType }) {
            base.append((equipmentType, equipmentType.replacingOccurrences(of: "_", with: " ").capitalized))
        }
        return base
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $name).focused($focus, equals: .name)
                    Picker("Type", selection: $equipmentType) {
                        ForEach(typeChoices, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    TextField("Notes (seat height, pin position ...)", text: $notes, axis: .vertical)
                        .lineLimit(1...3).focused($focus, equals: .notes)
                } header: { Text("\(item.exerciseName) @ \(gymName)") }

                stepsSection
                generatorSection

                Section {
                    if let errorText { Label(errorText, systemImage: "xmark.octagon.fill").font(.footnote).foregroundStyle(.red) }
                    if !canWrite {
                        Label("Needs a connection. Machines are saved on the server.", systemImage: "icloud.slash")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } footer: {
                    Text("Saving re-plans your upcoming sessions with these steps.")
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(item.variant == nil ? "Set up machine" : "Edit machine")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { Task { await save() } } label: {
                        if saving { ProgressView() } else { Text("Save").fontWeight(.semibold) }
                    }
                    .disabled(!canSave)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focus = nil }
                }
            }
            .task(id: GeneratorKey(start: start, step: step, count: count)) { await refreshPreview() }
        }
        .interactiveDismissDisabled(saving)
    }

    // MARK: Steps

    private var stepsSection: some View {
        Section {
            if ladder.isEmpty {
                Text(MachineLogic.needsLadder(equipmentType) ? "No steps yet. Generate them below or add them one by one."
                                                             : "Optional for this type of machine.")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(ladder, id: \.self) { kg in
                        WeightChip(kg: kg) { ladder = LadderRules.removing(kg, from: ladder) }
                    }
                }
                .padding(.vertical, 4)
                .accessibilityIdentifier("ladderChips")
            }
            HStack {
                TextField("Add a step (kg)", text: $manualText)
                    .keyboardType(.decimalPad)
                    .focused($focus, equals: .manual)
                    .onSubmit(addManual)
                Button("Add", action: addManual)
                    .buttonStyle(.bordered)
                    .disabled(LadderRules.parse(manualText) == nil || ladder.count >= LadderRules.maxSteps)
            }
            if let previous = item.previous?.weightLadder, !previous.isEmpty {
                Button {
                    ladder = LadderRules.normalize(previous)
                    syncGenerator(to: ladder)
                } label: {
                    Label("Copy from \(item.previous?.gymName ?? "previous gym") (\(previous.count) steps)", systemImage: "doc.on.doc")
                }
            }
            if !ladder.isEmpty {
                Button("Clear all steps", role: .destructive) { ladder = [] }
            }
        } header: {
            HStack {
                Text("Weight steps")
                Spacer()
                if let summary = LadderRules.summary(ladder) { Text(summary).textCase(nil).monospacedDigit() }
            }
        } footer: {
            Text("Tap a step to remove it. Add half-steps or an odd top weight by hand.")
        }
    }

    private var generatorSection: some View {
        Section {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(LadderRules.presets) { preset in
                        Button(preset.title) {
                            startText = Fmt.number(preset.start); stepText = Fmt.number(preset.step); count = preset.count
                        }
                        .buttonStyle(.bordered).controlSize(.small)
                    }
                }
            }
            HStack(spacing: 12) {
                labeledField("Start (kg)", text: $startText, field: .start)
                labeledField("Step (kg)", text: $stepText, field: .step)
            }
            Stepper(value: $count, in: 1...LadderRules.maxSteps) {
                Text("Steps: \(count)").monospacedDigit()
            }
            if preview.isEmpty {
                Text("Enter a start and a step above zero.").font(.footnote).foregroundStyle(.secondary)
            } else {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(preview, id: \.self) { WeightChip(kg: $0, muted: true) }
                }
                .accessibilityIdentifier("ladderPreview")
                Button {
                    ladder = LadderRules.normalize(preview)
                } label: {
                    Label(ladder.isEmpty ? "Use these steps" : "Replace steps with these", systemImage: "arrow.up.doc")
                }
                .buttonStyle(.borderedProminent).tint(Domain.lifting.color)
            }
        } header: { Text("Generator") } footer: {
            Text("Start + step, \(count) times. Values are rounded to 0.25 kg, at most \(LadderRules.maxSteps) steps.")
        }
    }

    private func labeledField(_ title: String, text: Binding<String>, field: Field) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField(title, text: text)
                .keyboardType(.decimalPad)
                .focused($focus, equals: field)
                .padding(8)
                .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
    }

    // MARK: Actions

    private func addManual() {
        guard let value = LadderRules.parse(manualText) else { return }
        ladder = LadderRules.adding(value, to: ladder)
        manualText = ""
    }

    private func syncGenerator(to ladder: [Double]) {
        let seed = LadderRules.generatorSeed(for: ladder)
        startText = Fmt.number(seed.start); stepText = Fmt.number(seed.step); count = seed.count
    }

    /// Instant local preview, then the server's answer (same rule) when online.
    private func refreshPreview() async {
        preview = LadderRules.generate(start: start, step: step, count: count)
        guard start > 0, step > 0 else { return }
        try? await Task.sleep(for: .milliseconds(250))
        if Task.isCancelled { return }
        let remote = await env.gyms.previewLadder(start: start, step: step, count: count)
        if !Task.isCancelled { preview = remote }
    }

    private func save() async {
        saving = true
        errorText = nil
        defer { saving = false }
        do {
            let saved = try await env.gyms.saveMachine(gymId: gymId, item: item, draft: draft)
            onSaved(saved)
            dismiss()
        } catch {
            errorText = GymError.map(error).errorDescription
        }
    }
}
