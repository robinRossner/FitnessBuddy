import SwiftData
import SwiftUI

struct SettingsView: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    @Query(filter: #Predicate<CachedGym> { $0.deletedAt == nil }, sort: \CachedGym.name) private var gyms: [CachedGym]
    @Query private var states: [SyncState]

    @State private var testing = false
    @State private var testResult: TestResult?

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

    var body: some View {
        @Bindable var settings = env.settings
        NavigationStack {
            Form {
                Section {
                    TextField("Backend URL", text: $settings.backendURL, prompt: Text("http://192.168.1.10:8000"))
                        .textContentType(.URL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("API token", text: $settings.apiToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            Text("Test connection")
                            Spacer()
                            if testing { ProgressView() }
                            else if let testResult {
                                Image(systemName: testResult.isOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                                    .foregroundStyle(testResult.isOK ? .green : .red)
                            }
                        }
                    }
                    .disabled(testing || env.settings.makeClient() == nil)
                    if let testResult {
                        Text(testResult.message).font(.footnote).foregroundStyle(.secondary)
                    }
                } header: { Text("Backend") } footer: {
                    Text("Plain http:// works for local-network addresses only. Use https:// anywhere else.")
                }

                Section("Training") {
                    Picker("Current gym", selection: $settings.currentGymId) {
                        Text("Not set").tag(Int?.none)
                        ForEach(gyms) { gym in Text(gym.name).tag(Int?.some(gym.id)) }
                    }
                    Stepper(value: $settings.restSeconds, in: 30...600, step: 15) {
                        LabeledContent("Rest timer", value: Fmt.duration(TimeInterval(settings.restSeconds)))
                    }
                }

                Section("Sync") {
                    LabeledContent("Status", value: statusText)
                    LabeledContent("Last sync") {
                        if let last = env.sync.lastSyncAt ?? states.first?.lastSyncAt {
                            Text(last, format: .relative(presentation: .named))
                        } else {
                            Text("Never")
                        }
                    }
                    Button("Sync now") { env.sync.request(.manual) }
                        .disabled(env.sync.status == .syncing || !env.settings.isConfigured)
                }

                #if DEBUG
                Section("Debug") {
                    Button("Load demo data") { DemoData.load(into: env.context, settings: env.settings) }
                    Button("Clear demo data", role: .destructive) { DemoData.clear(in: env.context) }
                }
                #endif

                Section("About") {
                    LabeledContent("Version", value: appVersion)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .onDisappear { env.sync.request(.settings) }
        }
    }

    private var statusText: String {
        switch env.sync.status {
        case .idle: "Up to date"
        case .syncing: "Syncing…"
        case .offline: "Offline"
        case .notConfigured: "Not configured"
        case .failed(let m): "Failed: \(m)"
        }
    }

    private func testConnection() async {
        guard let client = env.settings.makeClient() else { return }
        testing = true
        defer { testing = false }
        do {
            let health = try await client.health()
            // authenticated call: proves the token
            _ = try await client.readiness()
            testResult = .ok("Connected. Backend \(health.version ?? "?"), token accepted.")
        } catch {
            testResult = .failed((error as? APIError)?.errorDescription ?? error.localizedDescription)
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
