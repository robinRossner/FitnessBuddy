import SwiftData
import SwiftUI

struct SettingsView: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    @Query private var states: [SyncState]

    // Backend fields are edited as a draft and applied explicitly ("Save & sync" / Done).
    @State private var draftURL = ""
    @State private var draftToken = ""
    @State private var loadedDraft = false

    @State private var testing = false
    @State private var testResult: TestResult?
    @State private var saving = false
    @State private var saveNote: String?

    @State private var confirmSave = false
    @State private var confirmSaveThenDismiss = false
    @State private var confirmReset = false
    @State private var unsynced = 0
    @State private var discardAlert = false

    enum TestResult: Equatable {
        case ok(String)
        case failed(String)
    }

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let v = info?["CFBundleShortVersionString"] as? String ?? "?"
        let b = info?["CFBundleVersion"] as? String ?? "?"
        return "\(v) (\(b))"
    }

    private var state: SyncState? { states.first }
    private var draftValid: Bool {
        APIClient.normalizedBaseURL(draftURL) != nil
            && !draftToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    private var draftChanged: Bool {
        draftURL.trimmingCharacters(in: .whitespacesAndNewlines) != env.settings.backendURL
            || draftToken.trimmingCharacters(in: .whitespacesAndNewlines) != env.settings.apiToken
    }
    private var draftClient: APIClient? {
        guard let url = APIClient.normalizedBaseURL(draftURL) else { return nil }
        let token = draftToken.trimmingCharacters(in: .whitespacesAndNewlines)
        return APIClient(baseURL: url, token: token.isEmpty ? nil : token)
    }

    var body: some View {
        NavigationStack {
            Form {
                backendSection
                GymSettingsSection()
                Section {
                    Label("Rest times are set per exercise: tap the timer next to an exercise during a workout.", systemImage: "timer")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Text("Training") }
                syncSection
                coachSection

                #if DEBUG
                Section {
                    Button("Load demo data") { DemoData.load(into: env.context, settings: env.settings); env.sync.didResetData() }
                    Button("Clear demo data", role: .destructive) { env.clearDemoData() }
                } header: { Text("Debug") } footer: {
                    Text("Demo data is local only: sync is paused while it is loaded, and it is removed as soon as a backend is saved.")
                }
                #endif

                Section("About") {
                    LabeledContent("Version", value: appVersion)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { done() } }
            }
            .task {
                guard !loadedDraft else { return }
                loadedDraft = true
                draftURL = env.settings.backendURL
                draftToken = env.settings.apiToken
            }
            .confirmationDialog("Switch backend?", isPresented: $confirmSave, titleVisibility: .visible) {
                Button("Switch and sync") { Task { await save(thenDismiss: confirmSaveThenDismiss) } }
                Button("Cancel", role: .cancel) {}
            } message: { Text(switchWarning) }
            .confirmationDialog("Reset local data?", isPresented: $confirmReset, titleVisibility: .visible) {
                Button("Reset and resync", role: .destructive) { Task { await resetAndResync() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("All synced and cached data on this device is deleted and downloaded again from the backend."
                     + (unsynced > 0 ? " \(unsynced) unsynced record\(unsynced == 1 ? "" : "s") stay on this device and are sent afterwards." : ""))
            }
            .alert("Backend not saved", isPresented: $discardAlert) {
                Button("Discard changes", role: .destructive) { dismiss() }
                Button("Keep editing", role: .cancel) {}
            } message: { Text("Enter a valid URL and an API token, then tap Save & sync.") }
            .interactiveDismissDisabled(draftChanged)
            .onDisappear { env.sync.request(.settings) }
        }
    }

    // MARK: Sections

    private var backendSection: some View {
        Section {
            TextField("Backend URL", text: $draftURL,
                      prompt: Text("https://your-server.example.com").foregroundStyle(.secondary))
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            SecureField("API token", text: $draftToken)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button {
                Task { await testConnection() }
            } label: {
                HStack {
                    Text("Test connection")
                    Spacer()
                    if testing { ProgressView() }
                }
            }
            .disabled(testing || draftClient == nil)
            if let testResult {
                Label(testResult.message, systemImage: testResult.isOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                    .font(.footnote)
                    .foregroundStyle(testResult.isOK ? .green : .red)
            }
            Button {
                requestSave(thenDismiss: false)
            } label: {
                HStack {
                    Text("Save & sync").bold()
                    Spacer()
                    if saving { ProgressView() }
                }
            }
            .disabled(saving || !draftValid || !draftChanged)
            if let saveNote {
                Text(saveNote).font(.footnote).foregroundStyle(.red)
            }
            if env.settings.backendURL.isEmpty == false, !draftChanged {
                Label("Saved: \(env.settings.backendURL)", systemImage: "checkmark.seal")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        } header: { Text("Backend") } footer: {
            Text("Changing the URL or token deletes the cached data from the old backend and downloads everything again. "
                 + "Plain http:// works for local-network addresses only. Use https:// anywhere else.")
        }
    }

    private var coachSection: some View {
        Section {
            ApprovalModePicker()
        } header: { Text("Coach") } footer: {
            Text("How proposed settings changes are handled. Every applied change becomes a config version you can roll back.")
        }
    }

    private var syncSection: some View {
        Section("Sync") {
            LabeledContent("Status", value: statusText)
            lastSyncRow
            Button("Sync now") { env.sync.request(.manual) }
                .disabled(env.sync.status == .syncing || !env.settings.isConfigured || (state?.demoActive ?? false))
            Button("Reset local data & resync", role: .destructive) {
                unsynced = env.unsyncedRecordCount
                confirmReset = true
            }
            .disabled(env.sync.status == .syncing || !env.settings.isConfigured || (state?.demoActive ?? false))
        }
    }

    @ViewBuilder private var lastSyncRow: some View {
        if let state, let attempt = state.lastAttemptAt {
            if let error = state.lastError {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Failed", systemImage: "xmark.octagon.fill").foregroundStyle(.red).font(.subheadline.weight(.semibold))
                    Text(attempt, format: .dateTime.day().month().hour().minute().second()).font(.footnote).foregroundStyle(.secondary)
                    Text(error).font(.footnote)
                    if let ok = state.lastSyncAt {
                        Text("Last success: \(ok.formatted(.dateTime.day().month().hour().minute()))")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Success", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.subheadline.weight(.semibold))
                    Text(attempt, format: .dateTime.day().month().hour().minute().second()).font(.footnote).foregroundStyle(.secondary)
                    if let summary = state.lastSummary { Text(summary).font(.footnote) }
                }
            }
        } else {
            LabeledContent("Last sync", value: "Never")
        }
    }

    private var statusText: String {
        switch env.sync.status {
        case .idle: "Up to date"
        case .syncing: "Syncing…"
        case .offline: "Offline"
        case .notConfigured: "Not configured"
        case .demo: "Demo data loaded (sync paused)"
        case .failed: "Failed"
        }
    }

    private var switchWarning: String {
        var parts: [String] = []
        if state?.demoActive == true { parts.append("The demo data is removed.") }
        parts.append("Cached data from the current backend is deleted and downloaded again.")
        if unsynced > 0 {
            parts.append("\(unsynced) unsynced record\(unsynced == 1 ? "" : "s") (logged workouts not yet uploaded) stay on this device and are sent to the new backend.")
        }
        return parts.joined(separator: " ")
    }

    // MARK: Actions

    private func done() {
        if !draftChanged { dismiss(); return }
        if draftValid { requestSave(thenDismiss: true) } else { discardAlert = true }
    }

    /// Warns first when unsynced real records exist and the save wipes the cache; otherwise applies straight away.
    private func requestSave(thenDismiss: Bool) {
        let wipes = env.backendChangeWipes(url: draftURL.trimmingCharacters(in: .whitespacesAndNewlines),
                                           token: draftToken.trimmingCharacters(in: .whitespacesAndNewlines))
        unsynced = env.unsyncedRecordCount
        if wipes && unsynced > 0 {
            confirmSaveThenDismiss = thenDismiss
            confirmSave = true
        } else {
            Task { await save(thenDismiss: thenDismiss) }
        }
    }

    private func save(thenDismiss: Bool) async {
        saving = true
        saveNote = nil
        let stored = await env.saveBackend(url: draftURL, token: draftToken)
        draftURL = env.settings.backendURL
        draftToken = env.settings.apiToken
        saving = false
        if !stored {
            saveNote = "Could not save the token to the Keychain. It works until the app is closed."
        } else if thenDismiss {
            dismiss()
        }
    }

    private func resetAndResync() async {
        await env.resetLocalDataAndResync()
    }

    private func testConnection() async {
        guard let client = draftClient else { return }
        testing = true
        testResult = nil
        defer { testing = false }
        let health: HealthDTO
        do { health = try await client.health() }
        catch {
            testResult = .failed("Server not reachable: \(APIError.message(for: error))")
            return
        }
        do {
            // authenticated call: proves the token
            _ = try await client.readiness()
            testResult = .ok("Connected. Server reachable (backend \(health.version ?? "?")), token accepted.")
        } catch APIError.unauthorized {
            testResult = .failed("Server reachable, but the token was rejected: Unauthorized, check the token.")
        } catch {
            testResult = .failed("Server reachable, token check failed: \(APIError.message(for: error))")
        }
    }
}

extension SettingsView.TestResult {
    var isOK: Bool { if case .ok = self { true } else { false } }
    var message: String {
        switch self {
        case .ok(let m), .failed(let m): m
        }
    }
}

#if DEBUG
#Preview("Settings") {
    let env = PreviewData.environment()
    return SettingsView().environment(env).modelContainer(env.container)
}
#endif
