import FitnessCore
import SwiftUI

// MARK: - Design kit (Apple Health-like look of the five tabs)
//
// Light grouped background, one accent per area (`Domain` in Theme.swift: lifting orange, running teal-green,
// recovery/readiness indigo, heart pink, sleep cyan, body blue, activity green, coach purple, insights teal),
// coloured title-case card headers, big rounded numerals with a small grey unit, explanations behind (i) buttons
// instead of paragraphs. Chevrons appear ONLY on things that navigate.
//
// Screen scaffold
//   ScrollView { VStack(alignment: .leading, spacing: Kit.groupSpacing) { PageSection(...) ... }.pagePadding() }
//       .screenStyle()                          grouped background; .navigationTitle("Title") as usual
//   PageSection("Pinned", info: "...") { cards } trailing: { Button("Edit") {...} }
//                                            big bold title-case group heading, optional (i) and trailing control
// Cards
//   Card(padding: 16) { ... }                 the one card container (Components.swift); `tint:` = legacy edge bar
//   CardHeader(title:symbol:color:time:chevron:info:)
//                                            first row of a card: coloured label, grey time/context, chevron if it
//                                            navigates, optional (i)
//   MetricCard(metric:points:destination:)   value + sparkline card for a MetricInfo (or the raw init with
//                                            value/unit/time/sparkline); taps through to `destination`
//   MetricTile(label:symbol:color:value:unit:info:)
//                                            small stat on a 2-column grid; never navigates
//   HighlightCard(title:symbol:color:sentence:bars:destination:)
//                                            one sentence + two comparison bars (HighlightBar), navigates
//   ChevronRowLink(destination:label:)       a row inside a card that navigates (content + chevron)
//   ShowAllLink(title:destination:)          "Show All Data" footer row with divider, for chart cards
//   Chip(text:color:symbol:)                 small coloured capsule ("PR", "+2.5 kg", "Deload") (Components.swift)
//   GarminSyncButton(resources:)             toolbar "Sync" (Garmin pull + app sync); add .garminSyncBanner() to the screen
// Values
//   MetricText(value:unit:size:color:)       "78.2 kg" the Apple way; use for every big number
//   Sparkline(values:color:lineWidth:showsEndDot:)
//                                            minimal line for cards (give it a frame, e.g. 110 x 36)
//   TrendBadge(delta:higherIsBetter:suffix:) arrow + change, green when the change is good, orange when bad
//   InfoButton(text:)                        (i) that shows `text` in a popover; replaces explanation paragraphs
// Detail screens (MetricDetailView.swift)
//   MetricDetailView(metric:points:baseline:)
//                                            W / M / 6M / Y chart + average/total + About, for any MetricInfo and
//                                            [MetricPoint] you pass in (bars or line per `metric.style`)
//   LiveMetricDetailView(metric:)            the same, loading the real long-range data for a MetricCatalog metric
//                                            (health 365 days, weekly volume 52 weeks, logged weights)
// Data (Core/Stats/MetricSeries.swift, MetricCatalog.swift)
//   MetricInfo / MetricPoint / MetricSeries  metric definition, points, bucketing, sparkline values, time labels
//   MetricCatalog.hrv / .restingHR / .sleep / .steps / .bodyweight / .runningDistance / .liftingSessions /
//   .liftingVolume, MetricCatalog.points(_:from: Sources(health:volume:localWeights:))
// Insights (InsightKit.swift)
//   InsightRow(item:)                        one-line correlation sentence + strength dots (home screens, lists)
//   InsightCardContent(item:)                headline, strength, scatter + trend line, note (wrap in a Card)
//   InsightDetailView(item:)                 full screen for one correlation
//   StrengthMeter(r:color:)                  three dots filled by |r|
//
// Charts: a BarMark with `width: .ratio(...)` needs a String (categorical) x value; an Int/Double x renders empty.

enum Kit {
    static let cardRadius: CGFloat = 16
    /// Space between PageSections on a scrolling page.
    static let groupSpacing: CGFloat = 28
}

// MARK: - Scaffold

extension View {
    /// Grouped background for a whole screen.
    func screenStyle() -> some View { background(Color.screenBackground) }

    /// Side gutters and bottom padding of a scrolling page body.
    func pagePadding() -> some View { padding(.horizontal, 16).padding(.top, 4).padding(.bottom, 24) }
}

/// Big title-case section heading ("Pinned", "Highlights") with an optional (i) and a trailing control.
struct PageSection<Content: View, Trailing: View>: View {
    let title: String
    var info: String?
    let trailing: Trailing
    let content: Content

    init(_ title: String, info: String? = nil,
         @ViewBuilder content: () -> Content, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.info = info
        self.content = content()
        self.trailing = trailing()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(title).font(.title2.weight(.bold)).accessibilityAddTraits(.isHeader)
                if let info { InfoButton(text: info) }
                Spacer(minLength: 8)
                trailing
            }
            content
        }
    }
}

extension PageSection where Trailing == EmptyView {
    init(_ title: String, info: String? = nil, @ViewBuilder content: () -> Content) {
        self.init(title, info: info, content: content, trailing: { EmptyView() })
    }
}

// MARK: - Card parts

/// Health's card header: coloured label left; time/context and (only when the card navigates) a chevron right.
struct CardHeader: View {
    let title: String
    let symbol: String
    let color: Color
    var time: String? = nil
    var chevron = false
    var info: String? = nil

    var body: some View {
        HStack(spacing: 6) {
            Label(title, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(color)
                .lineLimit(1)
            if let info { InfoButton(text: info) }
            Spacer(minLength: 8)
            if let time {
                Text(time).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            }
            if chevron { DisclosureChevron() }
        }
    }
}

struct DisclosureChevron: View {
    var body: some View {
        Image(systemName: "chevron.right")
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.tertiary)
            .accessibilityHidden(true)
    }
}

/// A row inside a card that navigates: content left, chevron right.
struct ChevronRowLink<Label: View, Destination: View>: View {
    @ViewBuilder var destination: Destination
    @ViewBuilder var label: Label

    var body: some View {
        NavigationLink {
            destination
        } label: {
            HStack(spacing: 12) {
                label
                Spacer(minLength: 8)
                DisclosureChevron()
            }
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// "Show All Data" footer row for chart cards.
struct ShowAllLink<Destination: View>: View {
    var title = "Show All Data"
    @ViewBuilder var destination: Destination

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            NavigationLink {
                destination
            } label: {
                HStack {
                    Text(title).font(.subheadline)
                    Spacer()
                    DisclosureChevron()
                }
                .padding(.top, 10)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}

// MARK: - Values

/// A metric value the Apple way: big rounded numerals in the primary colour, the unit small and grey.
struct MetricText: View {
    let value: String
    var unit: String? = nil
    var size: Font.TextStyle = .title
    var color: Color = .primary

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(value)
                .font(.system(size, design: .rounded).weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(color)
            if let unit, !unit.isEmpty {
                Text(unit)
                    .font(.system(.subheadline, design: .rounded).weight(.semibold))
                    .foregroundStyle(.secondary)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
        .accessibilityElement(children: .combine)
    }
}

/// Minimal line chart for summary cards. Details live on the drill-down screen. Draws nothing with fewer than 2 values.
struct Sparkline: View {
    let values: [Double]
    var color: Color
    var lineWidth: CGFloat = 2
    var showsEndDot = true

    var body: some View {
        GeometryReader { geo in
            let pts = points(in: geo.size)
            ZStack {
                Path { p in
                    guard let first = pts.first else { return }
                    p.move(to: first)
                    for pt in pts.dropFirst() { p.addLine(to: pt) }
                }
                .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
                if showsEndDot, let last = pts.last {
                    Circle().fill(color).frame(width: 7, height: 7).position(last)
                }
            }
        }
        .accessibilityHidden(true)
    }

    private func points(in size: CGSize) -> [CGPoint] {
        guard values.count > 1, let lo = values.min(), let hi = values.max() else { return [] }
        let span = max(hi - lo, 0.0001)
        let inset: CGFloat = 4
        return values.enumerated().map { i, v in
            CGPoint(x: inset + (size.width - 2 * inset) * CGFloat(i) / CGFloat(values.count - 1),
                    y: inset + (size.height - 2 * inset) * CGFloat(hi == lo ? 0.5 : 1 - (v - lo) / span))
        }
    }
}

/// Explanations go behind this instead of sitting on the card.
struct InfoButton: View {
    let text: String
    @State private var shown = false

    var body: some View {
        Button { shown = true } label: {
            Image(systemName: "info.circle")
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .accessibilityLabel("About")
        .accessibilityHint(text)
        .popover(isPresented: $shown) {
            Text(text)
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
                .padding()
                .frame(width: 280)
                .presentationCompactAdaptation(.popover)
        }
    }
}

/// Arrow coloured by whether the change is good (green) or bad (orange), not by its direction. Below 1 it reads as flat.
struct TrendBadge: View {
    let delta: Double
    let higherIsBetter: Bool
    var suffix = "%"

    var body: some View {
        let flat = abs(delta) < 1
        let good = flat || (delta > 0) == higherIsBetter
        HStack(spacing: 2) {
            Image(systemName: flat ? "arrow.right" : (delta > 0 ? "arrow.up" : "arrow.down"))
            Text("\(Fmt.number(abs(delta), maxFraction: 1)) \(suffix)")
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(flat ? Color.secondary : (good ? Color.green : Color.orange))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(flat ? "About the same" : "\(delta > 0 ? "Up" : "Down") \(Fmt.number(abs(delta), maxFraction: 1)) \(suffix)")
    }
}

// MARK: - Metric cards

/// One value plus sparkline; taps through to `destination` (usually a MetricDetailView).
struct MetricCard<Destination: View>: View {
    let title: String
    let symbol: String
    let color: Color
    let value: String
    var unit: String?
    var time: String?
    var sparkline: [Double]
    @ViewBuilder var destination: Destination

    var body: some View {
        NavigationLink {
            destination
        } label: {
            Card {
                CardHeader(title: title, symbol: symbol, color: color, time: time, chevron: true)
                HStack(alignment: .bottom) {
                    MetricText(value: value, unit: unit, size: .title)
                    Spacer(minLength: 8)
                    if sparkline.count >= 2 {
                        Sparkline(values: sparkline, color: color).frame(width: 110, height: 36)
                    }
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows the chart")
    }
}

extension MetricCard {
    /// Card for a catalog metric: latest value, when it is from, and the recent trend. "–" / "No data" when empty.
    init(metric: MetricInfo, points: [MetricPoint], now: Date = AppClock.instant, @ViewBuilder destination: () -> Destination) {
        let latest = MetricSeries.latest(points)
        self.init(title: metric.short, symbol: metric.symbol, color: metric.domain.color,
                  value: metric.format(latest?.value), unit: metric.unit,
                  time: MetricSeries.timeLabel(latest, metric: metric, now: now) ?? "No Data",
                  sparkline: MetricSeries.sparkline(points, metric: metric, now: now), destination: destination)
    }
}

/// Small stat on the background grid: coloured symbol, label, value. Never navigates.
struct MetricTile: View {
    let label: String
    let symbol: String
    let color: Color
    let value: String
    var unit: String? = nil
    var info: String? = nil

    var body: some View {
        Card(padding: 14) {
            HStack(spacing: 5) {
                Image(systemName: symbol).foregroundStyle(color).accessibilityHidden(true)
                Text(label).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8)
                if let info { InfoButton(text: info) }
                Spacer(minLength: 0)
            }
            .font(.subheadline.weight(.medium))
            MetricText(value: value, unit: unit, size: .title2)
        }
    }
}

// MARK: - Highlights


/// Health's highlight: one sentence and (usually two) comparison bars; opens the related chart.
struct HighlightCard<Destination: View>: View {
    let title: String
    let symbol: String
    let color: Color
    let sentence: String
    let bars: [HighlightBar]
    @ViewBuilder var destination: Destination

    var body: some View {
        NavigationLink {
            destination
        } label: {
            Card {
                CardHeader(title: title, symbol: symbol, color: color, chevron: true)
                Text(sentence)
                    .font(.title3.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(bars) { bar in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                MetricText(value: bar.value, unit: bar.unit, size: .title3,
                                           color: bar.highlighted ? color : .secondary)
                                Text(bar.label).font(.subheadline).foregroundStyle(.secondary)
                            }
                            Capsule()
                                .fill(bar.highlighted ? AnyShapeStyle(color.gradient) : AnyShapeStyle(Color(.systemGray4)))
                                .frame(height: 8)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .scaleEffect(x: max(0.02, min(1, bar.fraction)), anchor: .leading)
                                .accessibilityHidden(true)
                        }
                    }
                }
                .padding(.top, 4)
            }
        }
        .buttonStyle(.plain)
    }
}
