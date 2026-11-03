import FitnessCore
import SwiftUI

/// Add, rename and delete gyms; tap one to see its machines.
struct GymManageView: View {
    @Environment(AppEnvironment.self) private var env
    @State private var showAdd = false
    @State private var addName = ""
    @State private var renaming: GymSummaryDTO?
    @State private var renameText = ""
    @State private var errorMessage: String?
    @State private var busy = false

    private var gyms: [GymSummaryDTO] { env.gyms.list?.gyms ?? [] }
    private var currentId: Int? { env.gyms.currentGymId }

    var body: some View {
        List {
            Section {
                ForEach(gyms) { gym in
                    NavigationLink { GymMachinesView(gymId: gym.id, gymName: gym.name) } label: { row(gym) }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) { Task { await delete(gym) } } label: { Label("Delete", systemImage: "trash") }
                                .disabled(!env.gyms.canWrite)
                            Button { renameText = gym.name; renaming = gym } label: { Label("Rename", systemImage: "pencil") }
                                .tint(.blue)
                        }
                        .contextMenu {
                            Button("Rename", systemImage: "pencil") { renameText = gym.name; renaming = gym }
                            Button("Delete", systemImage: "trash", role: .destructive) { Task { await delete(gym) } }
                        }
                }
            } footer: {
                if let hint = env.gyms.writeBlockedHint {
                    Label("\(hint): gyms can only be changed online.", systemImage: "icloud.slash")
                } else {
                    Text("Swipe a gym to rename or delete it. A gym with logged sessions, or your current gym, can't be deleted.")
                }
            }
        }
        .overlay {
            if gyms.isEmpty {
                LoadableContent(env.gyms.isRefreshing ? .loading() : .empty(
                    title: "No gyms loaded", symbol: "building.2",
                    message: env.gyms.failure ?? "Open this screen with a connection once to load your gyms."), style: .screen)
            }
        }
        .navigationTitle("Manage gyms")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { addName = ""; showAdd = true } label: { Image(systemName: "plus") }
                    .disabled(!env.gyms.canWrite || busy)
                    .accessibilityLabel("Add gym")
            }
        }
        .task { await env.gyms.loadList() }
        .refreshable { await env.gyms.fetchList() }
        .alert("Add gym", isPresented: $showAdd) {
            TextField("Name", text: $addName)
            Button("Add") { Task { await add() } }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Then set up its machines.") }
        .alert("Rename gym", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $renameText)
            Button("Rename") { if let gym = renaming { Task { await rename(gym) } } }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Couldn't do that", isPresented: Binding(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(errorMessage ?? "") }
        .screenIdentifier(AXID.Summary.gymManage)
    }

    private func row(_ gym: GymSummaryDTO) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(gym.name).font(.body)
                Text(subtitle(gym)).font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if gym.id == currentId { Chip(text: "Current", color: Domain.lifting.color) }
        }
        .frame(minHeight: 44)
        .accessibilityElement(children: .combine)
    }

    private func subtitle(_ gym: GymSummaryDTO) -> String {
        var parts: [String] = []
        if let n = gym.machineCount { parts.append("\(n) machine\(n == 1 ? "" : "s")") }
        if gym.source == "import" { parts.append("from your history") }
        return parts.joined(separator: " · ")
    }

    // MARK: Actions

    private func add() async {
        let name = addName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        await run { _ = try await env.gyms.addGym(name: name) }
    }

    private func rename(_ gym: GymSummaryDTO) async {
        let name = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != gym.name else { return }
        await run { try await env.gyms.renameGym(id: gym.id, to: name) }
    }

    private func delete(_ gym: GymSummaryDTO) async {
        await run { try await env.gyms.deleteGym(id: gym.id) }
    }

    private func run(_ action: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do { try await action() }
        catch { errorMessage = GymError.map(error).errorDescription }
    }
}
