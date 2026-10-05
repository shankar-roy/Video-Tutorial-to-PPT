import CoreLocation
import MapKit
import SwiftData
import SwiftUI

struct StaysView: View {
    @Environment(\.modelContext) private var context
    @EnvironmentObject private var model: AppModel
    @State private var showHome = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                StayMap(stays: model.stays, home: model.home)
                    .frame(height: 280)
                List {
                    if model.stays.isEmpty {
                        Text("Scan your library to turn photo locations into stays. Pictures without GPS are skipped and can be added to a day later.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(model.stays) { stay in
                        StayRow(stayId: stay.id)
                    }
                }
            }
            .navigationTitle("Stays")
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 8) {
                    if !model.status.isEmpty {
                        Text(model.status).font(.footnote).foregroundStyle(.secondary)
                    }
                    HStack {
                        Button("Scan Photos") { Task { await model.scanLibrary(context: context) } }
                        Button("Example") { Task { await model.loadExample(context: context) } }
                    }
                    HStack {
                        Button("Confirm home") { showHome = true }.disabled(model.stays.isEmpty && model.home == nil)
                        Button("Upload") { Task { await model.uploadStays() } }.disabled(model.stays.isEmpty)
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding()
                .background(.bar)
            }
            .sheet(isPresented: $showHome) {
                HomeConfirmView(proposal: model.homeProposal, existing: model.home) { home in
                    Task { await model.confirmHome(home, context: context) }
                }
            }
            .onChange(of: model.homeProposal != nil) { _, proposed in
                if proposed && model.home == nil { showHome = true }
            }
            .overlay { if model.busy { ProgressView().padding().background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 12)) } }
            .onDisappear {
                JournalCache.save(stays: model.stays, home: model.home, context: context)
            }
        }
    }
}

struct StayRow: View {
    @EnvironmentObject private var model: AppModel
    var stayId: String

    private var stay: StayCluster? {
        model.stays.first { $0.id == stayId }
    }

    var body: some View {
        if let stay {
            VStack(alignment: .leading, spacing: 4) {
                TextField("Place", text: Binding(
                    get: { model.stays.first { $0.id == stayId }?.placeName ?? "" },
                    set: { newValue in
                        guard let index = model.stays.firstIndex(where: { $0.id == stayId }) else { return }
                        model.stays[index].placeName = newValue
                    }
                ))
                .font(.headline)
                Text("\(stay.startAt.formatted(date: .abbreviated, time: .shortened)) – \(stay.endAt.formatted(date: .abbreviated, time: .omitted))")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text("\(stay.photos.count) photos")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct StayMap: View {
    var stays: [StayCluster]
    var home: HomeConfirmation?

    var body: some View {
        Map {
            ForEach(stays) { stay in
                Marker(stay.placeName.isEmpty ? "Stay" : stay.placeName, coordinate: stay.coordinate)
            }
            if let home {
                MapCircle(center: home.coordinate, radius: home.radiusKm * 1000)
                    .foregroundStyle(.blue.opacity(0.15))
                Marker(home.city.isEmpty ? "Home" : home.city, coordinate: home.coordinate)
            }
        }
    }
}

struct HomeConfirmView: View {
    var proposal: HomeProposal?
    var existing: HomeConfirmation?
    var onConfirm: (HomeConfirmation) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var city: String
    @State private var radius: Double
    @State private var latitude: Double
    @State private var longitude: Double

    init(proposal: HomeProposal?, existing: HomeConfirmation?, onConfirm: @escaping (HomeConfirmation) -> Void) {
        self.proposal = proposal
        self.existing = existing
        self.onConfirm = onConfirm
        if let existing {
            _city = State(initialValue: existing.city)
            _radius = State(initialValue: existing.radiusKm)
            _latitude = State(initialValue: existing.latitude)
            _longitude = State(initialValue: existing.longitude)
        } else if let proposal {
            _city = State(initialValue: "")
            _radius = State(initialValue: proposal.radiusKm)
            _latitude = State(initialValue: proposal.latitude)
            _longitude = State(initialValue: proposal.longitude)
        } else {
            _city = State(initialValue: "")
            _radius = State(initialValue: 25)
            _latitude = State(initialValue: 0)
            _longitude = State(initialValue: 0)
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("City", text: $city)
                VStack(alignment: .leading) {
                    Text("Radius \(Int(radius)) km")
                    Slider(value: $radius, in: 5...80, step: 1)
                }
                Text("Photos with the most nights suggest this home. You can change the city and how far “home” reaches.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .navigationTitle("Confirm home")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onConfirm(HomeConfirmation(city: city, latitude: latitude, longitude: longitude, radiusKm: radius))
                        dismiss()
                    }
                    .disabled(city.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .task {
                if city.isEmpty {
                    city = await PlaceGeocoder.city(latitude: latitude, longitude: longitude)
                }
            }
        }
    }
}

extension StayCluster {
    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
}

extension HomeConfirmation {
    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
}
