import SwiftData
import SwiftUI

// Cards of Summary's "Today" group: today's runs / rest day, the next lifting session, a running workout and the coach.

/// A planned run, compact: distance (or the interval structure), heart-rate zone and pace. Opens the full session sheet.
struct TodayRunCard: View {
    let item: PlanItemDTO
    @State private var showDetail: Bool

    init(item: PlanItemDTO, openDetail: Bool = false) {
        self.item = item
        _showDetail = State(initialValue: openDetail)
    }

    private var session: PrescriptionDTO? { item.session }
    private var type: String { session?.type ?? item.kind }

    var body: some View {
        Button { showDetail = true } label: {
            Card {
                CardHeader(title: PlanFormat.cardTitle(type: type, kind: item.kind, title: item.title),
                           symbol: PlanFormat.symbol(forKind: item.kind), color: Domain.running.color,
                           time: item.planStatus == .done ? "Done" : "Planned", chevron: true)
                if type == "intervals", let work = session?.structure?.work {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        MetricText(value: "\(work.reps ?? 0) × \(Fmt.number(work.distanceM ?? 0, maxFraction: 0))", unit: "m")
                        if let pace = work.paceLabel { secondary("@ \(pace) /km") }
                    }
                } else {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        if let km = session?.distanceKm ?? session?.totalKm {
                            MetricText(value: Fmt.km(km), unit: "km")
                        }
                        if let pace = PlanFormat.pace(session?.pace) { secondary("\(pace) /km") }
                    }
                }
                let hr = session?.hr ?? session?.structure?.work?.hr
                if HRZoneChip.label(for: hr) != nil { HRZoneChipView(hr: hr, compact: true) }
                if !item.adjustments.isEmpty {
                    AdjustmentNote(lines: item.adjustments, level: item.readiness?.level ?? .unknown)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityHint("Shows the full session")
        .sheet(isPresented: $showDetail) { RunDetailSheet(item: item) }
    }

    private func secondary(_ text: String) -> some View {
        Text(text).font(.subheadline).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.85)
    }
}

/// Today is a rest day.
struct RestDayCard: View {
    let item: PlanItemDTO

    var body: some View {
        Card {
            CardHeader(title: item.session?.optional == true ? "Optional Rest" : "Rest Day", symbol: "moon.zzz.fill",
                       color: Domain.recovery.color)
            Text(item.session?.optional == true ? "Easy movement is fine." : "Recover, eat, sleep.")
                .font(.subheadline).foregroundStyle(.secondary)
            ForEach(item.notes, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}

/// The next planned lifting session; opens its exercises with a Start button.
struct NextLiftCard: View {
    let row: CachedPlannedSession
    let when: String
    let subtitle: String

    var body: some View {
        let item = row.item
        NavigationLink {
            PlannedLiftView(row: row)
        } label: {
            Card {
                CardHeader(title: "Next Lift", symbol: Domain.lifting.symbol, color: Domain.lifting.color, time: when, chevron: true)
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(PlanFormat.cardTitle(type: item?.session?.type ?? "lifting", kind: row.kind, title: item?.title ?? row.title))
                        .font(.system(.title2, design: .rounded).weight(.semibold))
                        .lineLimit(1).minimumScaleFactor(0.8)
                    if !subtitle.isEmpty {
                        Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 0)
                }
                if item?.session?.gym != nil || item?.session?.deload?.active == true {
                    HStack(spacing: 6) {
                        if let gym = item?.session?.gym { Chip(text: gym, color: Domain.lifting.color) }
                        if item?.session?.deload?.active == true { Chip(text: "Deload", color: .purple) }
                    }
                }
                if let item, !item.adjustments.isEmpty {
                    AdjustmentNote(lines: item.adjustments, level: item.readiness?.level ?? .unknown)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityHint("Shows the exercises")
    }
}

/// Today's lifting session is finished.
struct LiftDoneCard: View {
    let row: CachedPlannedSession
    let completed: LocalSession

    var body: some View {
        Card {
            CardHeader(title: PlanFormat.cardTitle(type: "lifting", kind: row.kind, title: row.item?.title ?? row.title),
                       symbol: PlanFormat.symbol(forKind: row.kind), color: Domain.lifting.color, time: "Today")
            Label("Done · \(Fmt.longDuration((completed.endedAt ?? .now).timeIntervalSince(completed.startedAt)))",
                  systemImage: "checkmark.circle.fill")
                .font(.headline).foregroundStyle(.green)
        }
    }
}

/// A workout is running (started here): back to it.
struct ResumeWorkoutCard: View {
    @Environment(AppEnvironment.self) private var env
    let session: LocalSession

    var body: some View {
        Card {
            CardHeader(title: "Workout", symbol: Domain.lifting.symbol, color: Domain.lifting.color,
                       time: "Since " + session.startedAt.formatted(date: .omitted, time: .shortened))
            Text(session.name.isEmpty ? "Your workout is still running." : session.name).font(.headline)
            Button { env.selectedTab = .workout } label: {
                Label("Resume Workout", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent).tint(Domain.lifting.color)
        }
    }
}

/// Only while proposals wait for a decision; the decision itself lives on Coach.
struct CoachSummaryCard: View {
    @Environment(AppEnvironment.self) private var env
    let pendingCount: Int
    let latestId: String?

    var body: some View {
        let proposal = pendingCount == 1 ? latestId.flatMap { env.coach.detail($0) } : nil
        Card {
            CardHeader(title: "Coach", symbol: Domain.coach.symbol, color: Domain.coach.color,
                       time: proposal?.createdAt?.formatted(.relative(presentation: .named)))
            Text(CoachLogic.bannerTitle(pendingCount: pendingCount)).font(.headline)
            if let text = proposal?.diagnosis ?? proposal?.notesForUser, !text.isEmpty {
                Text(text).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Button(pendingCount == 1 ? "Review Proposal" : "Review Proposals") {
                env.openCoach(proposal: pendingCount == 1 ? latestId : nil)
            }
            .buttonStyle(.borderedProminent)
            .tint(Domain.coach.color)
        }
    }
}

struct EmptyPlanCard: View {
    let isConfigured: Bool
    let openSettings: () -> Void

    var body: some View {
        Card {
            CardHeader(title: isConfigured ? "No Plan Yet" : "Connect Your Backend", symbol: "icloud.and.arrow.down", color: .secondary)
            Text(isConfigured ? "Pull down to sync. Once a plan has been fetched it stays available offline."
                              : "Add the backend URL and API token in Settings to load your weekly plan.")
                .font(.subheadline).foregroundStyle(.secondary)
            if !isConfigured {
                Button("Open Settings", action: openSettings).buttonStyle(.borderedProminent)
            }
        }
    }
}

struct AdjustmentNote: View {
    let lines: [String]
    let level: ReadinessLevel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(lines, id: \.self) { line in
                Label(line, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.primary)
                    .labelStyle(.titleAndIcon)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background((level == .unknown ? Color.orange : level.color).opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Planned lift

/// The exercises of a planned lifting session and the button that starts it (or goes back to the running workout).
struct PlannedLiftView: View {
    @Environment(AppEnvironment.self) private var env
    @Environment(\.dismiss) private var dismiss
    @Query(filter: #Predicate<LocalSession> { $0.endedAt == nil && $0.deletedAt == nil && $0.startedHere })
    private var active: [LocalSession]
    let row: CachedPlannedSession

    var body: some View {
        let item = row.item
        let exercises = (item?.session?.exercises ?? []).sorted { ($0.order ?? 0) < ($1.order ?? 0) }
        List {
            Section {
                VStack(alignment: .leading, spacing: 6) {
                    Text(AppClock.date(fromDay: row.date)?.formatted(.dateTime.weekday(.wide).day().month(.wide)) ?? row.date)
                        .font(.subheadline).foregroundStyle(.secondary)
                    HStack(spacing: 6) {
                        if let gym = item?.session?.gym { Chip(text: gym, color: Domain.lifting.color) }
                        if item?.session?.deload?.active == true { Chip(text: "Deload", color: .purple) }
                    }
                }
                if let item, !item.adjustments.isEmpty {
                    AdjustmentNote(lines: item.adjustments, level: item.readiness?.level ?? .unknown)
                }
                Button {
                    if active.first != nil { env.selectedTab = .workout } else { env.startWorkout(from: row) }
                    dismiss()
                } label: {
                    Label(active.first != nil ? "Resume Workout" : "Start Workout", systemImage: "play.fill")
                        .frame(maxWidth: .infinity, minHeight: 34)
                }
                .buttonStyle(.borderedProminent)
                .tint(Domain.lifting.color)
                .listRowSeparator(.hidden)
            }
            if !exercises.isEmpty {
                Section("Exercises") {
                    ForEach(Array(exercises.enumerated()), id: \.offset) { _, ex in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(ex.name.withoutGymSuffix).font(.headline)
                            let sets = PlanFormat.setsSummary(ex.sets)
                            if !sets.isEmpty { Text(sets).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary) }
                            if let last = PlanFormat.lastSummary(ex.last) {
                                Text("Last: \(last)").font(.caption).foregroundStyle(.tertiary)
                            }
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            if let notes = item?.notes, !notes.isEmpty {
                Section("Notes") {
                    ForEach(notes, id: \.self) { Text($0).font(.subheadline) }
                }
            }
        }
        .navigationTitle(PlanFormat.cardTitle(type: "lifting", kind: row.kind, title: item?.title ?? row.title))
        .navigationBarTitleDisplayMode(.inline)
    }
}
