# Train Ticket Booking Demo

本文件与 `requirements.yaml` 保持相同需求内容；以下 YAML 区块遵循需求元模型。

```yaml
id: ROOT
name: Train Ticket Booking Demo
description:
  text: A small website where visitors can register or sign in, search published trains, select a journey, and create
    a booking. Accounts, sessions, and confirmed bookings persist. Payment, cancellation, refunds, seat inventory,
    order history, and timetable administration are outside scope.
  image: []
scenarios: []
children:
  - id: REQ-1
    name: Public home page and account access
    description:
      text: The public header provides Register and Login when signed out, and the username and Sign out when signed
        in. Registration creates an account and session; login restores a session for an existing account.
      image: []
    scenarios: []
    children:
      - id: REQ-1.1
        name: Register a traveler account
        description:
          text: |-
            Function: Register displays Nationality, Name, Passport number, Passport expiration date, Date of birth, Gender, Username, Email address, Password, Confirm password, a Terms of service/Privacy policy checkbox, and Next step. All fields are required. Name must contain 2–100 non-whitespace characters; passport number must contain 6–30 ASCII letters, digits, or hyphens; birth date must be in the past and passport expiration in the future. Username must be 3–32 ASCII letters, digits, hyphens, or underscores. Email must be valid and at most 254 characters. Password must be 12–128 characters and include uppercase, lowercase, digit, and special characters; confirmation must match. Username and email are unique, with email compared case-insensitively. A valid submission saves the account, signs the user in, and returns home. Invalid or duplicate input shows an error and creates no account or session.

            Required system data: The system must allow creation of independent accounts with username `tb-user-<timestamp>-<random>`, an email generated from that username using a valid domain, password `Valid-password-123!`, name `Ticket User <timestamp>-<random>`, nationality `Vietnam`, passport number `P<timestamp-digits>`, passport expiration date `2035-12-31`, date of birth `1995-06-15`, and gender `Male`. Duplicate-account examples reuse the same username or email. Invalid examples include username `bad username!`, email `not-an-email`, password `short`, a mismatched confirmation, a missing passport number, and unchecked terms.
          image:
            - id: IMG-REGISTER
              reference: reference/register.png
              page: registration
        scenarios:
          - id: SCN-REQ-1.1-01
            name: Register a valid traveler
            steps:
              - id: STEP-REQ-1.1-01-01
                Given: A signed-out visitor has unique valid account and traveler details.
                When: The visitor completes Register, accepts the terms, and clicks Next step.
                Then: The home page shows the username and Sign out, including after reload.
                references:
                  - '\ref[image](IMG-REGISTER)'
          - id: SCN-REQ-1.1-02
            name: Reject invalid or duplicate details
            steps:
              - id: STEP-REQ-1.1-02-01
                Given: Registration contains missing, malformed, mismatched, or already-used values.
                When: The visitor clicks Next step.
                Then: Register shows the relevant error and no signed-in session is created.
                references:
                  - '\ref[image](IMG-REGISTER)'
        children: []
      - id: REQ-1.2
        name: Sign in with an existing account
        description:
          text: |-
            Function: Login provides Username or email, Password, and Login controls. The identifier is trimmed; email matching is case-insensitive and the password must match exactly. Valid credentials create a reload-persistent session and return home. Empty, unknown, or incorrect credentials show one generic error and leave the visitor signed out.

            Required system data: The system must contain an account created through REQ-1.1 with username `tb-user-<timestamp>-<random>`, an email generated from that username using a valid domain, and password `Valid-password-123!`. Valid login examples use that username or email, including values with leading and trailing spaces. Invalid examples use password `incorrect-password` or an email that does not belong to any account.
          image:
            - id: IMG-LOGIN
              reference: reference/login.png
              page: login
        scenarios:
          - id: SCN-REQ-1.2-01
            name: Sign in with username or email
            steps:
              - id: STEP-REQ-1.2-01-01
                Given: A saved account exists and the visitor is signed out.
                When: The visitor submits its username or email, optionally surrounded by spaces, and the correct
                  password.
                Then: The home page shows the saved username and Sign out, including after reload.
                references:
                  - '\ref[requirement](REQ-1.1)'
                  - '\ref[image](IMG-LOGIN)'
          - id: SCN-REQ-1.2-02
            name: Reject invalid credentials
            steps:
              - id: STEP-REQ-1.2-02-01
                Given: The identifier or password is missing or incorrect.
                When: The visitor clicks Login.
                Then: Login shows a generic error without revealing whether the account exists.
                references:
                  - '\ref[requirement](REQ-1.1)'
                  - '\ref[image](IMG-LOGIN)'
        children: []
  - id: REQ-2
    name: Search trains and select a journey
    description:
      text: Any visitor can search the timetable by origin, destination, and date. Results show matching trains
        and allow one journey to be opened for booking. Searching does not create an account or booking.
      image: []
    scenarios: []
    children:
      - id: REQ-2.1
        name: Search valid criteria and show matching trains
        description:
          text: |-
            Function: The home page provides required From, To, Date, and Search controls. Values are trimmed; From and To must differ case-insensitively, and Date must use a published date value. A valid search opens results showing the normalized criteria, result count, and matching train cards with train number, route, times, and Book. Each Book action has an accessible name identifying its train number. Invalid input stays on the home page with an error.

            Required system data: The system must contain a bookable train `G532` from `Shanghai` to `Beijing` on `Sun, May 31`, with Shanghai/ShanghaiHongqiao as its displayed departure location and Beijing/BeijingNan as its displayed arrival location. It must also contain a bookable train `G561` from `Beijing` to `Tianjin` on the same date. Both records must have stable displayed departure and arrival times. Invalid search examples omit From, To, or Date, or use `Shanghai` and ` shanghai ` as the same-city pair.
          image:
            - id: IMG-SEARCH-FORM
              reference: reference/search-form.png
              page: home
            - id: IMG-SEARCH-RESULTS
              reference: reference/search-results.png
              page: search-results
        scenarios:
          - id: SCN-REQ-2.1-01
            name: Show matching trains
            steps:
              - id: STEP-REQ-2.1-01-01
                Given: The timetable contains a train matching the entered route and date.
                When: The visitor searches with valid criteria, optionally surrounded by spaces.
                Then: Results show normalized criteria and only the matching train cards.
                references:
                  - '\ref[image](IMG-SEARCH-FORM)'
                  - '\ref[image](IMG-SEARCH-RESULTS)'
          - id: SCN-REQ-2.1-02
            name: Reject invalid criteria
            steps:
              - id: STEP-REQ-2.1-02-01
                Given: A field is missing, the cities are equal, or the date is invalid.
                When: The visitor clicks Search.
                Then: The home page shows an error and no result list.
                references:
                  - '\ref[image](IMG-SEARCH-FORM)'
                  - '\ref[image](IMG-SEARCH-RESULTS)'
        children: []
      - id: REQ-2.2
        name: Select a train and open its booking entry
        description:
          text: |-
            Function: Clicking the Book action identified by a train number opens the booking entry for that exact train and searched date, showing its train number, route, and times. The system must not substitute another result.

            Required system data: The system must provide the two bookable records defined in REQ-2.1: `G532` for `Shanghai` to `Beijing` and `G561` for `Beijing` to `Tianjin`, both on `Sun, May 31`.
          image:
            - id: IMG-SEARCH-RESULTS
            - id: IMG-BOOKING-PAGE
              reference: reference/booking-page.png
              page: booking
        scenarios:
          - id: SCN-REQ-2.2-01
            name: Open the selected train
            steps:
              - id: STEP-REQ-2.2-01-01
                Given: Search results contain a bookable train.
                When: The visitor clicks that train's Book button.
                Then: The booking entry shows the same train, route, times, and date.
                references:
                  - '\ref[requirement](REQ-2.1)'
                  - '\ref[image](IMG-SEARCH-RESULTS)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
        children: []
  - id: REQ-3
    name: Create and view a booking
    description:
      text: A signed-in user can review the selected train, enter passenger and ticket details, and confirm one
        persistent booking. Invalid or unauthorized actions create no booking.
      image: []
    scenarios: []
    children:
      - id: REQ-3.1
        name: Display a selected train on the protected booking page
        description:
          text: |-
            Function: For a signed-in user, the Booking page shows the selected train number, route, times, and date above Passenger information and the booking form. Opening the page is read-only. A signed-out visitor must sign in and cannot submit; missing or unavailable train context shows the REQ-2.2 error instead of the form.

            Required system data: The system must allow creation of a unique REQ-1.1 account and provide the bookable `G532` (`Shanghai` to `Beijing`) and `G561` (`Beijing` to `Tianjin`) journeys on `Sun, May 31`. The selected train summary must display the corresponding train number, cities or stations, times, and date. The signed-out case uses the same valid selected journey after the account signs out.
          image:
            - id: IMG-BOOKING-PAGE
        scenarios:
          - id: SCN-REQ-3.1-01
            name: Show the selected journey
            steps:
              - id: STEP-REQ-3.1-01-01
                Given: A signed-in traveler selected a published train.
                When: The Booking page opens.
                Then: It shows the selected train summary and Passenger information without creating a booking.
                references:
                  - '\ref[requirement](REQ-1)'
                  - '\ref[requirement](REQ-2)'
                  - '\ref[requirement](REQ-1.2)'
                  - '\ref[requirement](REQ-2.2)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
          - id: SCN-REQ-3.1-02
            name: Block a signed-out visitor
            steps:
              - id: STEP-REQ-3.1-02-01
                Given: A visitor has a valid selected journey but no session.
                When: The visitor opens its booking page.
                Then: The page requests sign-in and provides no working submission action.
                references:
                  - '\ref[requirement](REQ-1)'
                  - '\ref[requirement](REQ-2)'
                  - '\ref[requirement](REQ-1.2)'
                  - '\ref[requirement](REQ-2.2)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
        children: []
      - id: REQ-3.2
        name: Confirm passenger details and create one booking record
        description:
          text: |-
            Function: The booking form requires Passenger name, ID number, Nationality, Ticket class, Ticket type, and Terms of service acceptance. Trimmed name and nationality must contain 2–100 and 2–60 visible characters; ID number must contain 6–30 ASCII letters, digits, or hyphens. Place order opens a summary; Confirm creates exactly one booking and shows its booking number and submitted details. Invalid fields create no booking, and repeated confirmation or reload must keep the same record.

            Required system data: Each booking example uses a unique REQ-1.1 account and an independent selected journey. The system must provide `G532` from `Shanghai` to `Beijing` and `G561` from `Beijing` to `Tianjin` on `Sun, May 31`. Valid booking data is (1) passenger `Nguyen Duc Minh`, ID `C612345677`, nationality `Vietnam`, class `standing ticket`, type `Adult`; and (2) passenger `Chen Li`, ID `D712345678`, nationality `Vietnam`, class `Business Class`, type `Adult`. Invalid examples use name `A`, ID `12345`, an empty nationality, or unchecked terms. Ticket class and type are selected explicitly rather than relying on defaults. Confirmation state and booking IDs must remain independent between accounts.
          image:
            - id: IMG-BOOKING-PAGE
        scenarios:
          - id: SCN-REQ-3.2-01
            name: Create one valid booking
            steps:
              - id: STEP-REQ-3.2-01-01
                Given: A signed-in traveler has an isolated account and selected journey.
                When: The traveler enters valid details, accepts the terms, reviews the summary, and confirms.
                Then: One booking number and the submitted journey, passenger, and ticket details are shown and
                  persist after reload.
                references:
                  - '\ref[requirement](REQ-1)'
                  - '\ref[requirement](REQ-2)'
                  - '\ref[requirement](REQ-3.1)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
          - id: SCN-REQ-3.2-02
            name: Reject invalid passenger details
            steps:
              - id: STEP-REQ-3.2-02-01
                Given: A required field is missing or invalid, or terms are not accepted.
                When: The traveler clicks Place order.
                Then: The form shows an error and opens neither the confirmation summary nor a booking.
                references:
                  - '\ref[requirement](REQ-1)'
                  - '\ref[requirement](REQ-2)'
                  - '\ref[requirement](REQ-3.1)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
          - id: SCN-REQ-3.2-03
            name: Prevent duplicate confirmation
            steps:
              - id: STEP-REQ-3.2-03-01
                Given: A valid confirmation has already succeeded.
                When: The traveler repeats Confirm or reloads the completed page.
                Then: The original booking number remains and no second booking is created.
                references:
                  - '\ref[requirement](REQ-1)'
                  - '\ref[requirement](REQ-2)'
                  - '\ref[requirement](REQ-3.1)'
                  - '\ref[image](IMG-BOOKING-PAGE)'
        children: []
```
