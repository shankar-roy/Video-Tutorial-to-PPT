import AuthenticationServices
import MapKit
import Photos
import PhotosUI
import SwiftData
import SwiftUI
import UIKit

struct TripListView: View {
    @Environment(\.modelContext) private var context
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            List {
                if model.member == nil {
                    Text("Sign in from Account to build a shared journal.")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.suggestions) { suggestion in
                    VStack(alignment: .leading, spacing: 8) {
                        Text("These look like the same trip")
                            .font(.headline)
                        Text(suggestion.titles.joined(separator: " · "))
                            .font(.subheadline)
                        HStack {
                            Button("Merge") { Task { await model.accept(suggestion) } }
                            Button("Dismiss") { Task { await model.dismiss(suggestion) } }
                        }
                    }
                }
                if model.trips.isEmpty {
                    Text("Confirm home, upload stays, sync tickets, then build the journal.")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.trips) { trip in
                    NavigationLink(trip.title) {
                        TripJournalView(tripId: trip.id)
                    }
                    .badge(trip.primaryCity)
                }
            }
            .navigationTitle("Journal")
            .toolbar {
                Button("Build") { Task { await model.buildTrips() } }
                    .disabled(model.member == nil)
            }
            .refreshable { await model.refreshTrips() }
            .task { await model.refreshTrips() }
            .overlay { if model.busy { ProgressView() } }
        }
    }
}

struct TripJournalView: View {
    @Environment(\.modelContext) private var context
    @EnvironmentObject private var model: AppModel
    var tripId: Int
    @State private var trip: TripDetailDTO?
    @State private var title = ""

    var body: some View {
        Group {
            if let trip {
                List {
                    Section {
                        TextField("Title", text: $title)
                            .onSubmit {
                                Task {
                                    if let updated = await model.saveTitle(tripId: trip.id, title: title, context: context) {
                                        self.trip = updated
                                    }
                                }
                            }
                        TripRouteMap(route: trip.route)
                            .frame(height: 220)
                            .listRowInsets(EdgeInsets())
                    }
                    ForEach(trip.days) { day in
                        DaySection(tripId: trip.id, day: day) { updated in
                            self.trip = updated
                        }
                    }
                }
                .navigationTitle(trip.primaryCity.isEmpty ? "Trip" : trip.primaryCity)
            } else {
                ProgressView()
            }
        }
        .task {
            trip = await model.loadTrip(tripId, context: context)
            title = trip?.title ?? ""
        }
    }
}

struct TripRouteMap: View {
    var route: [RoutePointDTO]

    var body: some View {
        Map {
            if route.count >= 2 {
                MapPolyline(coordinates: route.map(\.coordinate))
                    .stroke(.blue, lineWidth: 3)
            }
            ForEach(Array(route.enumerated()), id: \.offset) { _, point in
                Marker(point.name, coordinate: point.coordinate)
            }
        }
    }
}

struct DaySection: View {
    @Environment(\.modelContext) private var context
    @EnvironmentObject private var model: AppModel
    var tripId: Int
    var day: JournalDayDTO
    var onUpdate: (TripDetailDTO) -> Void
    @State private var text = ""
    @State private var pickerItems: [PhotosPickerItem] = []

    var body: some View {
        Section(day.day) {
            if !day.placeNames.isEmpty {
                Text(day.placeNames.joined(separator: ", "))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            ForEach(Array(day.flights.enumerated()), id: \.offset) { _, flight in
                VStack(alignment: .leading) {
                    Text("\(flight.airline) \(flight.flightNumber)")
                        .font(.headline)
                    Text("\(flight.originIata) → \(flight.destinationIata)")
                    if !flight.confirmationCode.isEmpty {
                        Text(flight.confirmationCode).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            ForEach(Array(day.lodgings.enumerated()), id: \.offset) { _, lodging in
                Text("Checked in at \(lodging.name)")
            }
            if !day.photos.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(day.photos) { photo in
                            JournalPhotoView(photo: photo)
                        }
                    }
                }
            }
            TextEditor(text: $text)
                .frame(minHeight: 80)
            Button("Save day") {
                Task {
                    if let updated = await model.saveDay(tripId: tripId, day: day.day, body: text, context: context) {
                        onUpdate(updated)
                    }
                }
            }
            PhotosPicker(selection: $pickerItems, maxSelectionCount: 6, matching: .images) {
                Text("Add photos")
            }
            .onChange(of: pickerItems) { _, items in
                Task { await upload(items) }
            }
        }
        .onAppear { text = day.body }
        .onChange(of: day.body) { _, newValue in text = newValue }
    }

    private func upload(_ items: [PhotosPickerItem]) async {
        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let image = UIImage(data: data) else { continue }
            let assetId = item.itemIdentifier ?? "picked-\(UUID().uuidString)"
            let asset = PHAsset.fetchAssets(withLocalIdentifiers: [assetId], options: nil).firstObject
            let takenAt = asset?.creationDate ?? Date()
            let coordinate = asset?.location?.coordinate
            if let updated = try? await model.api.uploadThumbnail(
                tripId: tripId,
                day: day.day,
                assetId: assetId,
                takenAt: takenAt,
                latitude: coordinate?.latitude ?? day.photos.first?.latitude ?? 0,
                longitude: coordinate?.longitude ?? day.photos.first?.longitude ?? 0,
                image: image
            ) {
                JournalCache.save(trip: updated, context: context)
                onUpdate(updated)
            }
        }
        pickerItems = []
    }
}

struct JournalPhotoView: View {
    @EnvironmentObject private var model: AppModel
    var photo: JournalPhotoDTO
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                RoundedRectangle(cornerRadius: 8).fill(.gray.opacity(0.2))
            }
        }
        .frame(width: 96, height: 96)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .task {
            if let local = PhotoLibraryScanner.thumbnail(assetId: photo.assetId) {
                image = local
                return
            }
            if let path = photo.thumbnailUrl {
                image = try? await model.api.fetchImage(path)
            }
        }
    }
}

struct AccountView: View {
    @EnvironmentObject private var model: AppModel
    @State private var invite = ""
    @State private var devName = ""
    @State private var baseURL = UserDefaults.standard.string(forKey: "apiBaseURL") ?? "http://127.0.0.1:8000"

    var body: some View {
        NavigationStack {
            Form {
                if let member = model.member {
                    Section("Household") {
                        Text(member.displayName.isEmpty ? "Signed in" : member.displayName)
                        Text("Invite code \(member.inviteCode)")
                            .font(.body.monospaced())
                            .textSelection(.enabled)
                    }
                    Section("Join another household") {
                        TextField("Invite code", text: $invite)
                        Button("Join") { Task { await model.joinHousehold(code: invite) } }
                            .disabled(invite.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                    Section("Tickets") {
                        Button("Sync Gmail") { Task { await model.syncGmail() } }
                        Text("Sync reads flight and hotel mail once, then drops the Google token.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Section {
                        Button("Sign out", role: .destructive) { model.signOut() }
                    }
                } else {
                    Section("Sign in") {
                        SignInWithAppleButton(.signIn) { request in
                            request.requestedScopes = [.fullName, .email]
                        } onCompletion: { result in
                            guard case .success(let auth) = result,
                                  let credential = auth.credential as? ASAuthorizationAppleIDCredential,
                                  let data = credential.identityToken,
                                  let token = String(data: data, encoding: .utf8) else { return }
                            let name = [credential.fullName?.givenName, credential.fullName?.familyName]
                                .compactMap { $0 }
                                .joined(separator: " ")
                            Task { await model.signIn(identityToken: token, displayName: name.isEmpty ? nil : name) }
                        }
                        .frame(height: 44)
                    }
                    if baseURL.hasPrefix("http://") {
                        Section("This network only") {
                            TextField("Name", text: $devName)
                            Button("Development sign in") {
                                Task { await model.devSignIn(name: devName.isEmpty ? "Traveler" : devName) }
                            }
                        }
                    }
                }
                Section("Server") {
                    TextField("API address", text: $baseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .onSubmit {
                            UserDefaults.standard.set(baseURL, forKey: "apiBaseURL")
                        }
                }
                if !model.status.isEmpty {
                    Section {
                        Text(model.status).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Account")
            .onDisappear {
                UserDefaults.standard.set(baseURL, forKey: "apiBaseURL")
            }
        }
    }
}

extension RoutePointDTO {
    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }
}
