import SwiftUI

/// What an "Ask coach" question is about.
struct AskCoachContext: Identifiable, Equatable {
    var scope: CoachScope
    var refId: String?
    /// "Incline Dumbbell Press", "Easy run, Oct 24", "This week".
    var subject: String
    /// The Coach tab lets the user pick general / week; entry points on a lift or run do not.
    var allowsScopeSwitch = false

    var id: String { scope.rawValue + "|" + (refId ?? "") }

    static let general = AskCoachContext(scope: .general, refId: nil, subject: "Training in general", allowsScopeSwitch: true)
    /// The backend accepts any day of the week as `ref_id`, so today identifies this week.
    static var thisWeek: AskCoachContext { .init(scope: .week, refId: AppClock.todayString, subject: "This week", allowsScopeSwitch: true) }
    static func lift(id: Int, name: String) -> AskCoachContext { .init(scope: .lift, refId: String(id), subject: name) }
    static func run(id: String, name: String) -> AskCoachContext { .init(scope: .run, refId: id, subject: name) }

    var symbol: String {
        switch scope {
        case .lift: "dumbbell.fill"
        case .run: "figure.run"
        case .week: "calendar"
        case .general: "figure.mixed.cardio"
        }
    }
}

/// The one sheet behind every "Ask coach" button: a context chip, a question with suggestions, Send.
/// After sending it opens the new (queued) proposal.
struct AskCoachSheet: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    @State private var context: AskCoachContext
    @State private var question = ""
    @State private var sending = false
    @State private var error: String?
    @FocusState private var focused: Bool

    init(context: AskCoachContext) { _context = State(initialValue: context) }

    private var trimmed: String { question.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var blocked: String? { env.coach.writeBlockedHint }
    private var canSend: Bool { !trimmed.isEmpty && !sending && blocked == nil }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    contextChip
                    if context.allowsScopeSwitch {
                        Picker("About", selection: scopeBinding) {
                            Text("General").tag(CoachScope.general)
                            Text("This week").tag(CoachScope.week)
                        }
                        .pickerStyle(.segmented)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel(title: "Your question", symbol: "bubble.left.fill", color: Domain.coach.color)
                        TextField("What do you want to know?", text: $question, axis: .vertical)
                            .lineLimit(4...8)
                            .focused($focused)
                            .padding(12)
                            .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                    suggestionChips
                    if let blocked { OfflineHint(text: blocked) }
                    if let error {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(.orange)
                    }
                    Text("The coach looks at your recent training and may suggest settings changes (rep ranges, steps, deloads). It never picks weights or paces, and nothing changes until you approve it.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .padding(16)
            }
            .background(Color.screenBackground)
            .navigationTitle("Ask coach")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button { send() } label: {
                        if sending { ProgressView() } else { Text("Send").bold() }
                    }
                    .disabled(!canSend)
                }
            }
            .task { await env.coach.load() }
        }
        .presentationDetents([.large])
    }

    private var scopeBinding: Binding<CoachScope> {
        Binding(get: { context.scope }, set: { newScope in
            context = newScope == .week ? .thisWeek : .general
        })
    }

    private var contextChip: some View {
        HStack(spacing: 8) {
            Image(systemName: context.symbol)
            Text(context.subject).lineLimit(1)
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(Domain.coach.color)
        .padding(.horizontal, 12).padding(.vertical, 7)
        .background(Domain.coach.color.opacity(0.14), in: Capsule())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("About: \(context.subject)")
    }

    private var suggestionChips: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(CoachLogic.suggestions(for: context.scope), id: \.self) { text in
                Button {
                    question = text
                    focused = true
                } label: {
                    Label(text, systemImage: "text.bubble")
                        .font(.subheadline)
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .foregroundStyle(.primary)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func send() {
        guard canSend else { return }
        sending = true
        error = nil
        Task {
            do {
                let proposal = try await env.coach.ask(scope: context.scope, refId: context.refId, question: trimmed)
                dismiss()
                env.openCoach(proposal: proposal.id)
            } catch {
                self.error = CoachError.map(error).errorDescription
            }
            sending = false
        }
    }
}

/// "Ask coach about this lift" button that owns its sheet.
struct AskCoachButton: View {
    let title: String
    let context: AskCoachContext
    @State private var show = false

    var body: some View {
        Button { show = true } label: {
            Label(title, systemImage: "sparkles")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.bordered)
        .tint(Domain.coach.color)
        .sheet(isPresented: $show) { AskCoachSheet(context: context) }
    }
}
