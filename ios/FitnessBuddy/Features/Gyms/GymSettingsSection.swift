import FitnessCore
import SwiftData
import SwiftUI

/// Settings > Gym: the current gym, switching it, its machines and the gym list.
struct GymSettingsSection: View {
    @Environment(AppEnvironment.self) private var env
    @Query(filter: #Predicate<CachedGym> { $0.deletedAt == nil }, sort: \CachedGym.name) private var cachedGyms: [CachedGym]
    @State private var switching = false
    @State private var errorMessage: String?

    private var currentId: Int? { env.gyms.currentGymId }

    /// Gyms from the server list, else the ones synced to this device (offline before the first list load).
    private var choices: [(id: Int, name: String)] {
        if let list = env.gyms.list?.gyms, !list.isEmpty { return list.map { ($0.id, $0.name) } }
        return cachedGyms.map { ($0.id, $0.name) }
    }

    private var currentName: String? {
        guard let currentId else { return nil }
        return choices.first { $0.id == currentId }?.name
    }

    var body: some View {
        Section {
            currentRow
            Picker("Change gym", selection: Binding(get: { currentId }, set: { change(to: $0) })) {
                if currentId == nil { Text("Not set").tag(Int?.none) }
                ForEach(choices, id: \.id) { Text($0.name).tag(Int?.some($0.id)) }
            }
            .disabled(!env.gyms.canWrite || switching || choices.isEmpty)
            if let text = MachineLogic.needsSetupText(env.gyms.unmappedCount), let id = currentId, let name = currentName {
                NavigationLink { GymMachinesView(gymId: id, gymName: name) } label: {
                    Label(text, systemImage: "exclamationmark.circle.fill").foregroundStyle(.orange)
                }
            }
            if let id = currentId, let name = currentName {
                NavigationLink { GymMachinesView(gymId: id, gymName: name) } label: {
                    LabeledContent("Machines at \(name)", value: progressText(for: id))
                }
            }
            NavigationLink("Manage gyms") { GymManageView() }
            if let errorMessage {
                Label(errorMessage, systemImage: "xmark.octagon.fill").font(.footnote).foregroundStyle(.red)
            }
        } header: { Text("Gym") } footer: {
            if let hint = env.gyms.writeBlockedHint {
                Label("\(hint) to switch gyms or change machines.", systemImage: "icloud.slash")
            } else {
                Text("Machine, cable and smith exercises are set up per gym. Barbell, dumbbell and bodyweight exercises are shared. Switching re-plans your upcoming sessions.")
            }
        }
        .task {
            await env.gyms.loadList()
            if let id = env.gyms.currentGymId { await env.gyms.loadMachines(gymId: id) }
        }
    }

    private var currentRow: some View {
        HStack(spacing: 12) {
            Image(systemName: "building.2.fill").font(.title2).foregroundStyle(Domain.lifting.color).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("Current gym").font(.caption).foregroundStyle(.secondary)
                Text(currentName ?? "Not set").font(.title3.weight(.semibold))
            }
            Spacer(minLength: 0)
            if switching { ProgressView() }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private func progressText(for id: Int) -> String {
        guard let list = env.gyms.machineList(id) else { return "–" }
        let p = MachineLogic.progress(list.items)
        return p.total == 0 ? "–" : "\(p.done) of \(p.total) set up"
    }

    private func change(to id: Int?) {
        guard let id, id != currentId else { return }
        Task {
            switching = true
            errorMessage = nil
            defer { switching = false }
            do {
                try await env.gyms.switchGym(to: id)
                await env.gyms.loadMachines(gymId: id)
            } catch { errorMessage = GymError.map(error).errorDescription }
        }
    }
}
