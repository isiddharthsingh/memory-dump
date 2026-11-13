import MapKit
import SwiftUI

/// Finds where a saved place is from its name and address, on the phone, and
/// remembers the answer: a place's coordinates don't change, and MapKit
/// limits how many searches an app may make.
enum PlaceLocator {
  enum Answer {
    case found(CLLocationCoordinate2D)
    /// MapKit knows no such place; asking again won't find it.
    case unknown
    /// No answer now (offline, or too many searches): try another time.
    case later
  }

  private static let file = URL.cachesDirectory.appending(path: "place-coordinates.json")
  /// Each query's latitude and longitude; empty for a place MapKit doesn't know.
  private static var known: [String: [Double]] =
    (try? Data(contentsOf: file)).flatMap { try? JSONDecoder().decode([String: [Double]].self, from: $0) } ?? [:]

  static func remembered(_ query: String) -> Answer? {
    guard let spot = known[query] else { return nil }
    return spot.count == 2 ? .found(.init(latitude: spot[0], longitude: spot[1])) : .unknown
  }

  static func locate(_ query: String) async -> Answer {
    if let answer = remembered(query) { return answer }
    let request = MKLocalSearch.Request()
    request.naturalLanguageQuery = query
    do {
      let found = try await MKLocalSearch(request: request).start().mapItems.first?.location.coordinate
      remember(query, found.map { [$0.latitude, $0.longitude] } ?? [])
      return found.map(Answer.found) ?? .unknown
    } catch let error as MKError where error.code == .placemarkNotFound {
      remember(query, [])
      return .unknown
    } catch {
      return .later
    }
  }

  private static func remember(_ query: String, _ spot: [Double]) {
    known[query] = spot
    try? JSONEncoder().encode(known).write(to: file, options: .atomic)
  }
}

@Observable
final class PlacesModel {
  struct Pin: Identifiable {
    let place: SavedPlace
    let coordinate: CLLocationCoordinate2D
    var id: String { place.id }
  }

  private(set) var places: [SavedPlace]?
  private(set) var pins: [Pin] = []
  private(set) var error: Error?
  /// Still asking MapKit where the places are.
  private(set) var locating = false

  /// MapKit takes about 50 searches a minute and then refuses for about a
  /// minute. A refusal is waited out this long, this many times in a row,
  /// before the rest is left for the next time the map opens (offline, say).
  private static let pause = Duration.seconds(20)
  private static let pauses = 4

  /// The saved copy first, then the server's list, then the search for the
  /// places not yet located: the fresh list never waits on MapKit.
  func load() async {
    if places == nil, let saved = try? await APIClient.saved.places() { show(saved) }
    do {
      show(try await APIClient.shared.places())
      error = nil
    } catch {
      if places == nil { self.error = error }
    }
    await locate()
  }

  /// The places already located go on the map at once.
  private func show(_ list: [SavedPlace]) {
    places = list
    pins = list.compactMap { place in
      if case .found(let spot)? = PlaceLocator.remembered(place.query) { Pin(place: place, coordinate: spot) } else { nil }
    }
  }

  /// Asks MapKit where each place without a pin is, one after another. Two
  /// saves naming one place share the answer.
  private func locate() async {
    locating = true
    defer { locating = false }
    var pausesLeft = Self.pauses
    for place in places ?? [] where !pins.contains(where: { $0.id == place.id }) {
      search: while true {
        if Task.isCancelled { return }
        switch await PlaceLocator.locate(place.query) {
        case .found(let spot):
          pins.append(Pin(place: place, coordinate: spot))
          pausesLeft = Self.pauses
          break search
        case .unknown:
          pausesLeft = Self.pauses
          break search
        case .later:
          // Throttled or offline: wait and ask for the same place again.
          guard pausesLeft > 0 else { return }
          pausesLeft -= 1
          try? await Task.sleep(for: Self.pause)
        }
      }
    }
  }
}

/// Every place the saved items name, as pins. Tapping one shows its save.
struct PlacesMapView: View {
  /// Opens a saved item, once the map has closed.
  let open: (UUID) -> Void
  @Environment(\.dismiss) private var dismiss
  @Environment(\.openURL) private var openURL
  @State private var model = PlacesModel()
  @State private var selected: String?
  @State private var position: MapCameraPosition = .automatic

  var body: some View {
    NavigationStack {
      Group {
        if let places = model.places {
          if places.isEmpty {
            ContentUnavailableView(
              "No places yet", systemImage: "mappin.slash",
              description: Text("Saves that name a restaurant, shop or sight show up here."))
          } else {
            map
          }
        } else if let error = model.error {
          ErrorNotice(title: "Couldn’t load your places", detail: error.localizedDescription) {
            Task { await model.load() }
          }
          .padding(20)
        } else {
          ProgressView()
        }
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
      .background(Palette.ground)
      .navigationTitle("Places")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
      }
    }
    .task { await model.load() }
  }

  /// Shows every pin there is so far, with room around the outermost ones;
  /// a single pin gets its neighbourhood. Not once the reader has moved the
  /// map or selected a pin: it is theirs to move by then.
  private func fit() {
    guard selected == nil, !position.positionedByUser, let first = model.pins.first else { return }
    let point = { (pin: PlacesModel.Pin) in MKMapRect(origin: MKMapPoint(pin.coordinate), size: MKMapSize()) }
    let all = model.pins.dropFirst().reduce(point(first)) { $0.union(point($1)) }
    let margin = max(all.width, all.height) * 0.2 + 1500 * MKMapPointsPerMeterAtLatitude(first.coordinate.latitude)
    withAnimation { position = .rect(all.insetBy(dx: -margin, dy: -margin)) }
  }

  /// How many of the places are on the map, while MapKit is still looking
  /// and when it couldn't find some.
  private var progress: String? {
    guard let places = model.places else { return nil }
    if model.locating { return "Finding \(Format.plural(places.count, "place"))…" }
    return model.pins.count == places.count ? nil : "\(model.pins.count) of \(places.count) found on the map"
  }

  private var map: some View {
    Map(position: $position, selection: $selected) {
      ForEach(model.pins) { pin in
        Marker(pin.place.name, systemImage: pin.place.done ? "checkmark" : "mappin", coordinate: pin.coordinate)
          .tint(pin.place.done ? Palette.ready : Palette.accent)
          .tag(pin.id)
      }
    }
    .safeAreaInset(edge: .bottom) {
      if let pin = model.pins.first(where: { $0.id == selected }) { card(pin.place) }
    }
    // On the map, under the bar: a bar's subtitle is lost against the map.
    .overlay(alignment: .top) {
      if let progress {
        Text(progress)
          .font(.footnote.weight(.semibold))
          .foregroundStyle(Palette.ink)
          .padding(.horizontal, 12)
          .padding(.vertical, 7)
          .background(.regularMaterial, in: .capsule)
          .padding(.top, 8)
      }
    }
    // The pins arrive one by one as MapKit finds them.
    .onChange(of: model.pins.count, initial: true) { fit() }
  }

  /// The selected pin: its place, the save it came from, and where to go next.
  private func card(_ place: SavedPlace) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      VStack(alignment: .leading, spacing: 3) {
        Text(place.name).font(.display(20, .heavy, relativeTo: .title3)).foregroundStyle(Palette.ink)
        if !place.address.isEmpty {
          Text(place.address).font(.subheadline).foregroundStyle(Palette.ink2)
        }
        if let title = place.title {
          Text("From “\(title)”").font(.footnote).foregroundStyle(Palette.ink3).lineLimit(2)
        }
      }
      HStack(spacing: 8) {
        Button {
          dismiss()
          open(place.item)
        } label: {
          Text("Open the save")
            .font(.subheadline.weight(.bold))
            .foregroundStyle(Palette.onInk)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(Palette.ink, in: .capsule)
        }
        Button {
          if let url = Format.mapsURL(place.query) { openURL(url) }
        } label: {
          Text("Open in Maps")
            .font(.subheadline.weight(.bold))
            .foregroundStyle(Palette.ink)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(Palette.fill, in: .capsule)
        }
      }
      .buttonStyle(PressableCardStyle())
    }
    .padding(16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Palette.raised, in: .rect(cornerRadius: 24))
    .overlay { RoundedRectangle(cornerRadius: 24).strokeBorder(Palette.raisedEdge) }
    .shadow(color: Palette.shadow(0.14), radius: 15, y: 8)
    .padding(.horizontal, 12)
    .padding(.bottom, 8)
  }
}
