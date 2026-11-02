import FitnessCore
import SwiftUI

/// The machines of one gym: the program first (by body region), then every other exercise.
struct GymMachinesView: View {
    @Environment(AppEnvironment.self) private var env
    let gymId: Int
    let gymName: String
    @State private var editing: MachineItemDTO?

    private var data: GymMachinesDTO? { env.gyms.machineList(gymId) }
    private var items: [MachineItemDTO] { data?.items ?? [] }

    var body: some View {
        let sections = MachineLogic.sections(items)
        List {
            Section { progressHeader }
            if let hint = env.gyms.writeBlockedHint, data != nil {
                Section {
                    Label("\(hint): you can look at the machines, but not change them.", systemImage: "icloud.slash")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            ForEach(sections.program) { group in
                Section {
                    ForEach(group.items) { row($0) }
                } header: {
                    VStack(alignment: .leading, spacing: 4) {
                        if group.id == sections.program.first?.id {
                            Text("In your program").font(.headline).foregroundStyle(.primary).textCase(nil)
                        }
                        Text(group.title)
                    }
                }
            }
            if !sections.other.isEmpty {
                Section("Other exercises") {
                    ForEach(sections.other) { row($0) }
                }
            }
        }
        .overlay {
            if data == nil {
                if env.gyms.isRefreshing {
                    ProgressView()
                } else {
                    ContentUnavailableView("No machines loaded", systemImage: "icloud.slash",
                                           description: Text(env.gyms.failure ?? "Open this screen with a connection once to load the machines."))
                }
            }
        }
        .navigationTitle(gymName)
        .navigationBarTitleDisplayMode(.inline)
        .task { await env.gyms.loadMachines(gymId: gymId) }
        .refreshable { await env.gyms.fetchMachines(gymId: gymId) }
        .sheet(item: $editing) { item in
            MachineEditorSheet(gymId: gymId, gymName: gymName, item: item)
        }
    }

    // MARK: Pieces

    private var progressHeader: some View {
        let progress = MachineLogic.progress(items)
        return VStack(alignment: .leading, spacing: 8) {
            Text(progress.text).font(.headline)
            ProgressView(value: progress.fraction).tint(progress.isComplete ? .green : Domain.lifting.color)
            if data != nil, let at = env.gyms.machinesFetchedAt[gymId], env.gyms.isOffline {
                Text(StatsFreshness.label(fetchedAt: at, offline: true) ?? "Offline").font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private func row(_ item: MachineItemDTO) -> some View {
        let setUp = MachineLogic.isSetUp(item)
        return Button { editing = item } label: {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.exerciseName).font(.body).foregroundStyle(.primary)
                    Label(MachineLogic.statusText(item), systemImage: setUp ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                        .font(.subheadline)
                        .foregroundStyle(setUp ? Color.green : Color.orange)
                    if let hint = MachineLogic.previousHint(item.previous) {
                        Text(hint).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the machine editor")
    }
}

#if DEBUG
#Preview("Machines") {
    let env = PreviewData.environment()
    return NavigationStack { GymMachinesView(gymId: 4, gymName: "Gym 4") }.environment(env).modelContainer(env.container)
}
#endif
