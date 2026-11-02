import Foundation

// Wire models of the `/api/v1/coach/*` endpoints (docs/api/coach.md once it exists). Decode with `CoachCoding.makeDecoder()`:
// keys are spelled out (config diffs contain free-form keys), enums have an `unknown` fallback and every optional
// block may be missing, so a small backend change never blanks the Coach tab.

public enum CoachCoding: Sendable {
    public static func makeDecoder() -> JSONDecoder { APICoding.makeRawKeyDecoder() }

    public static func makeEncoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys]
        return e
    }
}

// MARK: - Enums

public enum CoachStatus: String, TolerantEnum, Sendable {
    case awaitingKey = "awaiting_key", queued, running, pending, applied
    case partiallyApplied = "partially_applied", rejected, failed
    case dryRun = "dry_run", unknown
    public static var fallback: CoachStatus { .unknown }

    /// Waiting for the advisor (the app polls these).
    public var isInFlight: Bool { self == .queued || self == .running }
    public var title: String {
        switch self {
        case .awaitingKey: "Waiting for key"
        case .queued: "Queued"
        case .running: "Thinking"
        case .pending: "Needs review"
        case .applied: "Applied"
        case .partiallyApplied: "Partly applied"
        case .rejected: "Rejected"
        case .failed: "Failed"
        case .dryRun: "Dry run"
        case .unknown: "Unknown"
        }
    }
}

public enum CoachTriggerType: String, TolerantEnum, Sendable {
    case liftStall = "lift_stall", ruleConflict = "rule_conflict", runningStagnation = "running_stagnation"
    case dataAnomaly = "data_anomaly", weeklyReview = "weekly_review", ask, unknown
    case missedSession = "missed_session"
    public static var fallback: CoachTriggerType { .unknown }

    public var symbol: String {
        switch self {
        case .liftStall: "chart.line.flattrend.xyaxis"
        case .ruleConflict: "arrow.triangle.branch"
        case .runningStagnation: "figure.run"
        case .dataAnomaly: "waveform.path.ecg"
        case .weeklyReview: "calendar"
        case .ask: "bubble.left.and.text.bubble.right"
        case .missedSession: "calendar.badge.exclamationmark"
        case .unknown: "sparkles"
        }
    }
}

public enum CoachConfidence: String, TolerantEnum, Sendable {
    case low, medium, high, unknown
    public static var fallback: CoachConfidence { .unknown }
    public var title: String { self == .unknown ? "" : rawValue.capitalized + " confidence" }
}

public enum CoachRisk: String, TolerantEnum, Sendable {
    case low, medium, high, unknown
    public static var fallback: CoachRisk { .unknown }
    public var title: String { self == .unknown ? "Risk ?" : rawValue.capitalized + " risk" }
}

public enum CoachChangeStatus: String, TolerantEnum, Sendable {
    case proposed, applied, rejected, invalid, unknown
    public static var fallback: CoachChangeStatus { .unknown }
}

public enum CoachAuthor: String, TolerantEnum, Sendable {
    case engine, user, advisor, seed, unknown
    public static var fallback: CoachAuthor { .unknown }

    public var symbol: String {
        switch self {
        case .engine: "gearshape.fill"
        case .user: "person.fill"
        case .advisor: "sparkles"
        case .seed: "leaf.fill"
        case .unknown: "questionmark"
        }
    }
    public var title: String {
        switch self {
        case .engine: "Engine"
        case .user: "You"
        case .advisor: "Coach"
        case .seed: "Seed"
        case .unknown: "Unknown"
        }
    }
}

public enum CoachVerdict: String, TolerantEnum, Sendable {
    case improved, neutral, worse, unknown
    public static var fallback: CoachVerdict { .unknown }
}

public enum ApprovalMode: String, TolerantEnum, CaseIterable, Identifiable, Sendable {
    case manual, autoLowRisk = "auto-low-risk", auto, unknown
    public static var fallback: ApprovalMode { .unknown }
    public var id: String { rawValue }

    /// The modes the user can pick.
    public static var choices: [ApprovalMode] { [.manual, .autoLowRisk, .auto] }

    public var title: String {
        switch self {
        case .manual: "Manual"
        case .autoLowRisk: "Auto, low risk"
        case .auto: "Auto"
        case .unknown: "Unknown"
        }
    }
    public var explanation: String {
        switch self {
        case .manual: "You approve or reject every proposal."
        case .autoLowRisk: "Low-risk changes apply by themselves, the rest waits for you."
        case .auto: "Every valid change applies by itself. You can roll back in History."
        case .unknown: "Set by the server."
        }
    }
    public var symbol: String {
        switch self {
        case .manual: "hand.raised.fill"
        case .autoLowRisk: "shield.lefthalf.filled"
        case .auto: "bolt.fill"
        case .unknown: "questionmark"
        }
    }
}

// MARK: - Helpers

private extension KeyedDecodingContainer {
    /// A String that may arrive as a number.
    func flexString(_ key: Key) -> String? {
        if let s = try? decodeIfPresent(String.self, forKey: key) { return s }
        if let i = try? decodeIfPresent(Int.self, forKey: key) { return String(i) }
        return nil
    }
}

// MARK: - Proposals

public struct CoachTriggerRefDTO: Decodable, Sendable, Equatable {
    public var kind: String?
    public var id: String?

    public enum CodingKeys: String, CodingKey, Sendable { case kind, id }
    public init(kind: String? = nil, id: String? = nil) { self.kind = kind; self.id = id }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        id = c.flexString(.id)
    }
}

public struct CoachTriggerDTO: Decodable, Sendable, Equatable {
    public var type: CoachTriggerType
    public var title: String
    public var detail: String?
    public var ref: CoachTriggerRefDTO?

    public enum CodingKeys: String, CodingKey, Sendable { case type, title, detail, ref }
    public init(type: CoachTriggerType, title: String, detail: String? = nil, ref: CoachTriggerRefDTO? = nil) {
        self.type = type; self.title = title; self.detail = detail; self.ref = ref
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decodeIfPresent(CoachTriggerType.self, forKey: .type) ?? .unknown
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Coach proposal"
        detail = try? c.decodeIfPresent(String.self, forKey: .detail)
        ref = try? c.decodeIfPresent(CoachTriggerRefDTO.self, forKey: .ref)
    }
}

/// Something the user can switch on or off when approving (a parameter change or a schedule change).
public protocol CoachReviewable: Identifiable where ID == String {
    var valid: Bool { get }
    var status: CoachChangeStatus { get }
}

public struct CoachChangeDTO: Decodable, Sendable, Equatable, Identifiable, CoachReviewable {
    public var id: String
    public var param: String
    public var label: String
    public var from: JSONValue
    public var to: JSONValue
    public var reason: String
    public var risk: CoachRisk
    public var valid: Bool
    public var validationError: String?
    public var status: CoachChangeStatus

    public enum CodingKeys: String, CodingKey, Sendable {
        case id, param, label, from, to, reason, risk, valid, status
        case validationError = "validation_error"
    }
    public init(id: String, param: String, label: String? = nil, from: JSONValue = .null, to: JSONValue = .null, reason: String = "",
         risk: CoachRisk = .low, valid: Bool = true, validationError: String? = nil, status: CoachChangeStatus = .proposed) {
        self.id = id; self.param = param; self.label = label ?? param; self.from = from; self.to = to; self.reason = reason
        self.risk = risk; self.valid = valid; self.validationError = validationError; self.status = status
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        param = try c.decodeIfPresent(String.self, forKey: .param) ?? ""
        id = c.flexString(.id) ?? param
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? param
        from = try c.decodeIfPresent(JSONValue.self, forKey: .from) ?? .null
        to = try c.decodeIfPresent(JSONValue.self, forKey: .to) ?? .null
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        risk = try c.decodeIfPresent(CoachRisk.self, forKey: .risk) ?? .unknown
        valid = try c.decodeIfPresent(Bool.self, forKey: .valid) ?? true
        validationError = try? c.decodeIfPresent(String.self, forKey: .validationError)
        status = try c.decodeIfPresent(CoachChangeStatus.self, forKey: .status) ?? (valid ? .proposed : .invalid)
    }

    /// "7–12 → 8–12", with units where the parameter path implies one.
    public var changeText: String { CoachValueFormat.change(from: from, to: to, path: param) }
}

public enum ScheduleAction: String, TolerantEnum, Sendable {
    case move, skip, swap, shorten, replace, unknown
    public static var fallback: ScheduleAction { .unknown }

    public var symbol: String {
        switch self {
        case .move: "arrow.right.circle"
        case .skip: "forward.end.circle"
        case .swap: "arrow.left.arrow.right.circle"
        case .shorten: "arrow.down.right.and.arrow.up.left.circle"
        case .replace: "arrow.triangle.2.circlepath.circle"
        case .unknown: "calendar.circle"
        }
    }
}

/// One entry of `schedule_changes` on a proposal: move / skip / swap / shorten / replace a planned session.
/// Approved by id together with the parameter changes (`change_ids`).
public struct CoachScheduleChangeDTO: Decodable, Sendable, Equatable, Identifiable, CoachReviewable {
    public var id: String
    public var date: String?
    public var action: ScheduleAction
    public var target: String?
    public var toDate: String?
    public var newKind: String?
    public var distanceKm: Double?
    public var reason: String
    public var risk: CoachRisk
    public var valid: Bool
    public var validationError: String?
    public var status: CoachChangeStatus
    public var label: String

    public enum CodingKeys: String, CodingKey, Sendable {
        case id, date, action, target, reason, risk, valid, status, label
        case toDate = "to_date", newKind = "new_kind", distanceKm = "distance_km", validationError = "validation_error"
    }

    public init(id: String, date: String? = nil, action: ScheduleAction = .move, target: String? = nil, toDate: String? = nil,
         newKind: String? = nil, distanceKm: Double? = nil, reason: String = "", risk: CoachRisk = .low, valid: Bool = true,
         validationError: String? = nil, status: CoachChangeStatus = .proposed, label: String? = nil) {
        self.id = id; self.date = date; self.action = action; self.target = target; self.toDate = toDate; self.newKind = newKind
        self.distanceKm = distanceKm; self.reason = reason; self.risk = risk; self.valid = valid
        self.validationError = validationError; self.status = status
        self.label = label ?? Self.fallbackLabel(action: action, target: target, date: date, toDate: toDate)
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.flexString(.id) ?? ""
        date = try? c.decodeIfPresent(String.self, forKey: .date)
        action = try c.decodeIfPresent(ScheduleAction.self, forKey: .action) ?? .unknown
        target = c.flexString(.target)
        toDate = try? c.decodeIfPresent(String.self, forKey: .toDate)
        newKind = try? c.decodeIfPresent(String.self, forKey: .newKind)
        distanceKm = try? c.decodeIfPresent(Double.self, forKey: .distanceKm)
        reason = (try? c.decodeIfPresent(String.self, forKey: .reason)) ?? ""
        risk = (try? c.decodeIfPresent(CoachRisk.self, forKey: .risk)) ?? .unknown
        valid = (try? c.decodeIfPresent(Bool.self, forKey: .valid)) ?? true
        validationError = try? c.decodeIfPresent(String.self, forKey: .validationError)
        status = (try? c.decodeIfPresent(CoachChangeStatus.self, forKey: .status)) ?? (valid ? .proposed : .invalid)
        let given = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? nil
        label = given.flatMap { $0.isEmpty ? nil : $0 } ?? Self.fallbackLabel(action: action, target: target, date: date, toDate: toDate)
    }

    /// "Move intervals Fri → Sat" when the server sent no label.
    public static func fallbackLabel(action: ScheduleAction, target: String?, date: String?, toDate: String?) -> String {
        let what = (target ?? "session").replacingOccurrences(of: "_", with: " ")
        let verb = action == .unknown ? "Change" : action.rawValue.capitalized
        guard let date else { return "\(verb) \(what)" }
        if let toDate { return "\(verb) \(what) \(DayLabel.weekday(date)) → \(DayLabel.weekday(toDate))" }
        return "\(verb) \(what) \(DayLabel.weekday(date))"
    }
}

public struct CoachSessionOverrideDTO: Decodable, Sendable, Equatable, Identifiable {
    public var date: String?
    public var session: String?
    public var change: String
    public var reason: String?

    public var id: String { [date, session, change].compactMap { $0 }.joined(separator: "|") }

    public enum CodingKeys: String, CodingKey, Sendable { case date, session, change, reason }
    public init(date: String? = nil, session: String? = nil, change: String, reason: String? = nil) {
        self.date = date; self.session = session; self.change = change; self.reason = reason
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try? c.decodeIfPresent(String.self, forKey: .date)
        session = try? c.decodeIfPresent(String.self, forKey: .session)
        change = try c.decodeIfPresent(String.self, forKey: .change) ?? ""
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
    }
}

public struct CoachProposalDTO: Decodable, Sendable, Equatable, Identifiable {
    public var id: String
    public var createdAt: Date?
    public var status: CoachStatus
    public var trigger: CoachTriggerDTO
    public var question: String?
    public var diagnosis: String?
    public var notesForUser: String?
    public var confidence: CoachConfidence?
    public var changes: [CoachChangeDTO]
    public var sessionOverrides: [CoachSessionOverrideDTO]
    /// Moves / skips of planned sessions (additive; empty on older backends).
    public var scheduleChanges: [CoachScheduleChangeDTO]
    public var appliedConfigVersion: String?
    public var error: String?
    public var costUsd: Double?
    /// Only on `GET /coach/proposals/{id}`.
    public var inputSummary: JSONValue?

    /// Waits for a decision: `pending`, or `partially_applied` with changes still `proposed`
    /// (exactly what the server counts in `plan/today.coach`).
    public var isPending: Bool {
        switch status {
        case .pending: true
        case .partiallyApplied:
            changes.contains { $0.status == .proposed && $0.valid } || scheduleChanges.contains { $0.status == .proposed && $0.valid }
        default: false
        }
    }

    /// `awaiting_key`, `failed` and `dry_run` proposals can be dismissed (rejected) to clear them.
    public var canDismiss: Bool { status == .awaitingKey || status == .failed || status == .dryRun }

    public enum CodingKeys: String, CodingKey, Sendable {
        case id, status, trigger, question, diagnosis, confidence, changes, error
        case createdAt = "created_at", notesForUser = "notes_for_user", sessionOverrides = "session_overrides"
        case scheduleChanges = "schedule_changes"
        case appliedConfigVersion = "applied_config_version", costUsd = "cost_usd", inputSummary = "input_summary"
    }

    public init(id: String, createdAt: Date? = nil, status: CoachStatus, trigger: CoachTriggerDTO, question: String? = nil,
         diagnosis: String? = nil, notesForUser: String? = nil, confidence: CoachConfidence? = nil, changes: [CoachChangeDTO] = [],
         sessionOverrides: [CoachSessionOverrideDTO] = [], scheduleChanges: [CoachScheduleChangeDTO] = [],
         appliedConfigVersion: String? = nil, error: String? = nil, costUsd: Double? = nil, inputSummary: JSONValue? = nil) {
        self.scheduleChanges = scheduleChanges
        self.id = id; self.createdAt = createdAt; self.status = status; self.trigger = trigger; self.question = question
        self.diagnosis = diagnosis; self.notesForUser = notesForUser; self.confidence = confidence; self.changes = changes
        self.sessionOverrides = sessionOverrides; self.appliedConfigVersion = appliedConfigVersion; self.error = error
        self.costUsd = costUsd; self.inputSummary = inputSummary
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.flexString(.id) else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "id missing"))
        }
        self.id = id
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        status = try c.decodeIfPresent(CoachStatus.self, forKey: .status) ?? .unknown
        trigger = (try? c.decodeIfPresent(CoachTriggerDTO.self, forKey: .trigger))
            ?? CoachTriggerDTO(type: .unknown, title: "Coach proposal")
        question = try? c.decodeIfPresent(String.self, forKey: .question)
        diagnosis = try? c.decodeIfPresent(String.self, forKey: .diagnosis)
        notesForUser = try? c.decodeIfPresent(String.self, forKey: .notesForUser)
        confidence = try? c.decodeIfPresent(CoachConfidence.self, forKey: .confidence)
        changes = (try? c.decodeIfPresent([CoachChangeDTO].self, forKey: .changes)) ?? []
        sessionOverrides = (try? c.decodeIfPresent([CoachSessionOverrideDTO].self, forKey: .sessionOverrides)) ?? []
        scheduleChanges = (try? c.decodeIfPresent([CoachScheduleChangeDTO].self, forKey: .scheduleChanges)) ?? []
        appliedConfigVersion = c.flexString(.appliedConfigVersion)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        costUsd = try? c.decodeIfPresent(Double.self, forKey: .costUsd)
        inputSummary = try? c.decodeIfPresent(JSONValue.self, forKey: .inputSummary)
    }
}

public struct CoachProposalListDTO: Decodable, Sendable, Equatable {
    public var items: [CoachProposalDTO]

    public enum CodingKeys: String, CodingKey, Sendable { case items }
    public init(items: [CoachProposalDTO]) { self.items = items }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = try c.decodeIfPresent([CoachProposalDTO].self, forKey: .items) ?? []
    }
}

// MARK: - History

public struct CoachDiffEntryDTO: Sendable, Equatable, Identifiable {
    public var path: String
    public var old: JSONValue
    public var new: JSONValue
    public var id: String { path }

    public var changeText: String { CoachValueFormat.change(from: old, to: new, path: path) }
}

public struct CoachOutcomeDTO: Decodable, Sendable, Equatable {
    public var verdict: CoachVerdict
    public var notes: String?
    public var metricBefore: JSONValue?
    public var metricAfter: JSONValue?

    public enum CodingKeys: String, CodingKey, Sendable {
        case verdict, notes
        case metricBefore = "metric_before", metricAfter = "metric_after"
    }
    public init(verdict: CoachVerdict, notes: String? = nil, metricBefore: JSONValue? = nil, metricAfter: JSONValue? = nil) {
        self.verdict = verdict; self.notes = notes; self.metricBefore = metricBefore; self.metricAfter = metricAfter
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        verdict = try c.decodeIfPresent(CoachVerdict.self, forKey: .verdict) ?? .unknown
        notes = try? c.decodeIfPresent(String.self, forKey: .notes)
        metricBefore = try? c.decodeIfPresent(JSONValue.self, forKey: .metricBefore)
        metricAfter = try? c.decodeIfPresent(JSONValue.self, forKey: .metricAfter)
    }
}

extension CoachOutcomeDTO {
    public struct MetricRow: Equatable, Sendable { public var name: String; public var change: String }

    /// Server metrics are dicts keyed by metric name (`{"bench_press_e1rm_kg": 71.0}`); a bare value is shown unnamed.
    /// "bench press e1rm kg  70 → 71".
    public static func metricRows(before: JSONValue?, after: JSONValue?, path: String? = nil) -> [MetricRow] {
        func dict(_ v: JSONValue?) -> [String: JSONValue] { if case .object(let o) = v { o } else { [:] } }
        let b = dict(before), a = dict(after)
        if b.isEmpty && a.isEmpty {
            guard let before, let after, !before.isNull || !after.isNull else { return [] }
            return [MetricRow(name: "Metric", change: CoachValueFormat.change(from: before, to: after, path: path))]
        }
        return Set(b.keys).union(a.keys).sorted().map { key in
            let name = key.replacingOccurrences(of: "_", with: " ")
            return MetricRow(name: name, change: CoachValueFormat.change(from: b[key] ?? .null, to: a[key] ?? .null, path: key))
        }
    }
}

public struct CoachVersionDTO: Decodable, Sendable, Equatable, Identifiable {
    public var version: String
    public var createdAt: Date?
    public var author: CoachAuthor
    public var reason: String?
    public var diff: [CoachDiffEntryDTO]
    public var consultationId: String?
    public var outcome: CoachOutcomeDTO?
    public var active: Bool

    public var id: String { version }

    public enum CodingKeys: String, CodingKey, Sendable {
        case version, author, reason, diff, outcome, active
        case createdAt = "created_at", consultationId = "consultation_id"
    }
    private struct Pair: Decodable {
        var old: JSONValue
        var new: JSONValue
        enum CodingKeys: String, CodingKey { case old, new }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            old = try c.decodeIfPresent(JSONValue.self, forKey: .old) ?? .null
            new = try c.decodeIfPresent(JSONValue.self, forKey: .new) ?? .null
        }
    }

    public init(version: String, createdAt: Date? = nil, author: CoachAuthor, reason: String? = nil, diff: [CoachDiffEntryDTO] = [],
         consultationId: String? = nil, outcome: CoachOutcomeDTO? = nil, active: Bool = false) {
        self.version = version; self.createdAt = createdAt; self.author = author; self.reason = reason; self.diff = diff
        self.consultationId = consultationId; self.outcome = outcome; self.active = active
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let version = c.flexString(.version) else {
            throw DecodingError.keyNotFound(CodingKeys.version, .init(codingPath: c.codingPath, debugDescription: "version missing"))
        }
        self.version = version
        createdAt = try? c.decodeIfPresent(Date.self, forKey: .createdAt)
        author = try c.decodeIfPresent(CoachAuthor.self, forKey: .author) ?? .unknown
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        let pairs = (try? c.decodeIfPresent([String: Pair].self, forKey: .diff)) ?? [:]
        diff = pairs.keys.sorted().map { CoachDiffEntryDTO(path: $0, old: pairs[$0]!.old, new: pairs[$0]!.new) }
        consultationId = c.flexString(.consultationId)
        outcome = try? c.decodeIfPresent(CoachOutcomeDTO.self, forKey: .outcome)
        active = try c.decodeIfPresent(Bool.self, forKey: .active) ?? false
    }
}

public struct CoachHistoryDTO: Decodable, Sendable, Equatable {
    public var versions: [CoachVersionDTO]

    public enum CodingKeys: String, CodingKey, Sendable { case versions }
    public init(versions: [CoachVersionDTO]) { self.versions = versions }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        versions = try c.decodeIfPresent([CoachVersionDTO].self, forKey: .versions) ?? []
    }
}

// MARK: - Settings

public struct CoachSettingsDTO: Decodable, Sendable, Equatable {
    public var approvalMode: ApprovalMode
    public var apiKeyConfigured: Bool
    public var dryRun: Bool
    public var callsThisWeek: Int
    public var maxCallsPerWeek: Int
    /// Additive provider keys (docs/api/coach.md): `credential_configured` is true for an API key or a Claude
    /// subscription token, `provider` is `api | claude_code | none`, `billing` is `api | subscription`, `model` the
    /// configured model. Older servers do not send them.
    public var credentialConfigured: Bool?
    public var provider: String?
    public var billing: String?
    public var model: String?

    public enum CodingKeys: String, CodingKey, Sendable {
        case approvalMode = "approval_mode", apiKeyConfigured = "api_key_configured", dryRun = "dry_run"
        case callsThisWeek = "calls_this_week", maxCallsPerWeek = "max_calls_per_week"
        case credentialConfigured = "credential_configured", provider, billing, model
    }
    public init(approvalMode: ApprovalMode = .manual, apiKeyConfigured: Bool = false, dryRun: Bool = false,
         callsThisWeek: Int = 0, maxCallsPerWeek: Int = 0, credentialConfigured: Bool? = nil, provider: String? = nil,
         billing: String? = nil, model: String? = nil) {
        self.approvalMode = approvalMode; self.apiKeyConfigured = apiKeyConfigured; self.dryRun = dryRun
        self.callsThisWeek = callsThisWeek; self.maxCallsPerWeek = maxCallsPerWeek
        self.credentialConfigured = credentialConfigured; self.provider = provider; self.billing = billing; self.model = model
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        approvalMode = try c.decodeIfPresent(ApprovalMode.self, forKey: .approvalMode) ?? .manual
        apiKeyConfigured = try c.decodeIfPresent(Bool.self, forKey: .apiKeyConfigured) ?? false
        dryRun = try c.decodeIfPresent(Bool.self, forKey: .dryRun) ?? false
        callsThisWeek = (try? c.decodeIfPresent(FlexCount.self, forKey: .callsThisWeek))?.value ?? 0
        maxCallsPerWeek = (try? c.decodeIfPresent(FlexCount.self, forKey: .maxCallsPerWeek))?.value ?? 0
        credentialConfigured = try? c.decodeIfPresent(Bool.self, forKey: .credentialConfigured)
        provider = try? c.decodeIfPresent(String.self, forKey: .provider)
        billing = try? c.decodeIfPresent(String.self, forKey: .billing)
        model = try? c.decodeIfPresent(String.self, forKey: .model)
    }

    /// The advisor can run: an API key or a subscription token is set (falls back to the API key flag).
    public var isConnected: Bool { credentialConfigured ?? apiKeyConfigured }
}

/// `coach` block of `GET /plan/today` (decoded with the snake-case decoder, cached with the plan).
public struct CoachSummaryDTO: Codable, Sendable, Equatable {
    public var pendingCount: Int
    public var latestPendingId: String?

    public init(pendingCount: Int = 0, latestPendingId: String? = nil) {
        self.pendingCount = pendingCount; self.latestPendingId = latestPendingId
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pendingCount = (try? c.decodeIfPresent(FlexCount.self, forKey: .pendingCount))?.value ?? 0
        if let s = try? c.decodeIfPresent(String.self, forKey: .latestPendingId) { latestPendingId = s }
        else if let i = try? c.decodeIfPresent(Int.self, forKey: .latestPendingId) { latestPendingId = String(i) }
    }
}

// MARK: - Requests

public enum CoachScope: String, Codable, Sendable, CaseIterable {
    case lift, run, week, general
}

public struct CoachAskRequest: Encodable, Sendable, Equatable {
    public var scope: CoachScope
    public var refId: String?
    public var question: String
    public enum CodingKeys: String, CodingKey, Sendable { case scope, refId = "ref_id", question }

    public init(scope: CoachScope, refId: String? = nil, question: String) {
        self.scope = scope
        self.refId = refId
        self.question = question
    }
}

public struct CoachApproveRequest: Encodable, Sendable, Equatable {
    /// nil = every valid change.
    public var changeIds: [String]?
    public enum CodingKeys: String, CodingKey, Sendable { case changeIds = "change_ids" }

    public init(changeIds: [String]? = nil) { self.changeIds = changeIds }
}

public struct CoachRejectRequest: Encodable, Sendable, Equatable {
    public var note: String?

    public init(note: String? = nil) { self.note = note }
}

public struct CoachSettingsUpdate: Encodable, Sendable, Equatable {
    public var approvalMode: ApprovalMode
    public enum CodingKeys: String, CodingKey, Sendable { case approvalMode = "approval_mode" }

    public init(approvalMode: ApprovalMode) { self.approvalMode = approvalMode }
}
