import Foundation

/// Small pure rules behind the Coach screens (kept free of UI so they are unit-tested).
enum CoachLogic {
    // MARK: Selection

    /// Only valid, still-proposed changes can be switched on.
    static func isSelectable(_ change: CoachChangeDTO) -> Bool {
        change.valid && (change.status == .proposed || change.status == .unknown)
    }

    /// Default selection: every selectable change.
    static func defaultSelection(_ changes: [CoachChangeDTO]) -> Set<String> {
        Set(changes.filter(isSelectable).map(\.id))
    }

    /// Flip one change; invalid / unknown ids are ignored.
    static func toggled(_ id: String, in selection: Set<String>, changes: [CoachChangeDTO]) -> Set<String> {
        guard let change = changes.first(where: { $0.id == id }), isSelectable(change) else { return selection }
        var next = selection
        if next.contains(id) { next.remove(id) } else { next.insert(id) }
        return next
    }

    /// Ids to send, in the proposal's order, restricted to selectable changes.
    static func approvalIds(selection: Set<String>, changes: [CoachChangeDTO]) -> [String] {
        changes.filter { isSelectable($0) && selection.contains($0.id) }.map(\.id)
    }

    // MARK: Polling

    static let pollInterval: TimeInterval = 3
    static let pollTimeout: TimeInterval = 120

    /// Only queued / running proposals are worth polling; every other status is final for the app.
    static func shouldPoll(_ status: CoachStatus) -> Bool { status.isInFlight }

    // MARK: Today banner

    static func bannerVisible(pendingCount: Int) -> Bool { pendingCount > 0 }

    static func bannerTitle(pendingCount: Int) -> String {
        pendingCount == 1 ? "Coach has 1 proposal to review" : "Coach has \(pendingCount) proposals to review"
    }

    // MARK: Ask suggestions

    static func suggestions(for scope: CoachScope) -> [String] {
        switch scope {
        case .lift: ["Why am I stalling?", "Should I change my rep range?", "Is my increment too big?"]
        case .run: ["Is my easy pace right?", "Why was my heart rate so high?", "Should I change the intervals?"]
        case .week: ["Is this week's plan too much?", "Should I deload?", "How is my recovery looking?"]
        case .general: ["What should I change next?", "Is my training balanced?", "Am I progressing as expected?"]
        }
    }

    // MARK: Versions

    /// "v3.3" -> "v3.4", "v3" -> "v3.1", "3.9" -> "3.10". nil when the label has no number to bump.
    static func nextVersion(after label: String?) -> String? {
        guard let label, !label.isEmpty else { return nil }
        let prefix = label.hasPrefix("v") || label.hasPrefix("V") ? String(label.prefix(1)) : ""
        let body = String(label.dropFirst(prefix.count))
        var parts = body.split(separator: ".").map(String.init)
        guard !parts.isEmpty, parts.allSatisfy({ Int($0) != nil }) else { return nil }
        if parts.count == 1 { parts.append("1") }
        else { parts[parts.count - 1] = String(Int(parts[parts.count - 1])! + 1) }
        return prefix + parts.joined(separator: ".")
    }

    /// "v3.2" for display; adds the "v" when the backend sends a bare number.
    static func versionLabel(_ version: String) -> String {
        version.first?.isNumber == true ? "v" + version : version
    }

    // MARK: Grouping

    struct Groups: Equatable {
        var pending: [CoachProposalDTO] = []
        var inFlight: [CoachProposalDTO] = []
        var recent: [CoachProposalDTO] = []
    }

    /// Pending first, then queued / running / waiting for a key, then everything decided or failed (newest first).
    static func group(_ proposals: [CoachProposalDTO]) -> Groups {
        var g = Groups()
        for p in proposals.sorted(by: { ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast) }) {
            if p.isPending { g.pending.append(p); continue }
            switch p.status {
            case .queued, .running, .awaitingKey: g.inFlight.append(p)
            default: g.recent.append(p)
            }
        }
        return g
    }
}

/// Polls one proposal until it leaves queued / running or the timeout passes. Time is counted in whole intervals
/// (not wall clock), so the loop is deterministic under test and cheap to reason about.
@MainActor
enum CoachPoller {
    enum Outcome: Equatable { case finished(CoachStatus), timedOut, cancelled }

    @discardableResult
    static func run(interval: TimeInterval = CoachLogic.pollInterval, timeout: TimeInterval = CoachLogic.pollTimeout,
                    sleep: @Sendable (TimeInterval) async -> Void = { try? await Task.sleep(for: .seconds($0)) },
                    fetch: () async throws -> CoachProposalDTO,
                    onUpdate: (CoachProposalDTO) -> Void = { _ in }) async -> Outcome {
        var elapsed: TimeInterval = 0
        while elapsed < timeout {
            await sleep(interval)
            if Task.isCancelled { return .cancelled }
            elapsed += interval
            if let proposal = try? await fetch() {
                onUpdate(proposal)
                if !CoachLogic.shouldPoll(proposal.status) { return .finished(proposal.status) }
            }
            // a failed poll (offline, 5xx) just counts as a tick
        }
        return .timedOut
    }
}
