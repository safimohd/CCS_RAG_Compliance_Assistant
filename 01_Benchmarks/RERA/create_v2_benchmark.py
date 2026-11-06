"""
Creates Benchmark_RERA_V2_UserJourney_50Q.xlsx
50 questions following a homebuyer / promoter / agent user journey through RERA.
Tiers T1-T5, 10 questions each.
"""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from pathlib import Path

QUESTIONS = [
    # ── T1: Factual Recall (10Q) ──────────────────────────────────────────────
    {
        "question_id":   "V2-T1-01",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "A promoter is planning to launch a residential apartment project. "
            "Under RERA 2016, how many days before advertising or inviting bookings "
            "must the promoter apply for project registration with the RERA Authority?"
        ),
        "expected_answer": (
            "A promoter must apply for registration at least 3 months before "
            "advertising, marketing, booking, selling, or offering for sale any "
            "plot, apartment or building in the project (Section 3, RERA 2016)."
        ),
        "source_citation": "RERA 2016, Section 3(1)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-02",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "A homebuyer asks: what percentage of the total amount realised from "
            "allottees must a promoter deposit into the designated separate bank "
            "account under RERA?"
        ),
        "expected_answer": (
            "70% of the amounts realised from allottees for each project must be "
            "deposited in a separate account to be used only for construction costs "
            "and land costs of that project (Section 4(2)(l)(D), RERA 2016)."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(D)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-03",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "What is the maximum carpet area of an apartment above which a project "
            "cannot claim exemption from RERA registration on account of project size?"
        ),
        "expected_answer": (
            "RERA does not define an exemption by carpet area of individual units. "
            "The registration exemption thresholds under Section 3(2) are: (a) land "
            "area not exceeding 500 sq metres, or (b) number of apartments not "
            "exceeding 8 (inclusive of all phases). Both thresholds are alternative, "
            "not cumulative — satisfying either one exempts the project."
        ),
        "source_citation": "RERA 2016, Section 3(2)(a) and (b)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "T5 — question implies a carpet-area threshold that does not exist",
    },
    {
        "question_id":   "V2-T1-04",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "A real estate agent wants to facilitate bookings in a RERA-registered "
            "project. Under RERA 2016, is a real estate agent required to register "
            "with the RERA Authority, and if so, how long is the registration valid?"
        ),
        "expected_answer": (
            "Yes. Under Section 9 of RERA 2016, every real estate agent who intends "
            "to facilitate sale or purchase of plots, apartments or buildings in "
            "RERA-registered projects must register with the RERA Authority. "
            "Registration is valid for a period prescribed by the respective state "
            "rules (typically 5 years), and can be renewed."
        ),
        "source_citation": "RERA 2016, Section 9",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-05",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "Under RERA 2016, within how many days of receipt of a complaint must "
            "the RERA Authority pass an order on a complaint filed by an allottee "
            "against a promoter?"
        ),
        "expected_answer": (
            "RERA 2016 does not specify a fixed number of days for the Authority to "
            "decide a complaint. Section 31 provides for filing of complaints, and "
            "the Authority adjudicates as expeditiously as possible. However, the "
            "Adjudicating Officer must decide within 60 days under Section 71(5), "
            "and must record reasons if unable to do so."
        ),
        "source_citation": "RERA 2016, Sections 31 and 71(5)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-06",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "A promoter has received an order from the RERA Authority requiring "
            "payment to an allottee. The promoter disagrees. Under RERA 2016, "
            "which body does the promoter appeal to, and within how many days?"
        ),
        "expected_answer": (
            "The promoter may appeal to the Real Estate Appellate Tribunal (REAT) "
            "under Section 44 of RERA 2016. The appeal must be filed within 60 days "
            "of receipt of the Authority's or Adjudicating Officer's order. The "
            "Tribunal may condone delay if sufficient cause is shown."
        ),
        "source_citation": "RERA 2016, Section 44",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-07",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "What penalty does RERA 2016 prescribe for a promoter who fails to "
            "register a project that is mandatorily required to be registered, "
            "and continues to sell units from that unregistered project?"
        ),
        "expected_answer": (
            "Under Section 59 of RERA 2016, a promoter who violates Section 3 "
            "(mandatory registration) is liable to a penalty of up to 10% of the "
            "estimated cost of the real estate project. If the violation continues, "
            "the promoter may face imprisonment of up to 3 years, or a further "
            "penalty, or both."
        ),
        "source_citation": "RERA 2016, Section 59",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-08",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "Under RERA 2016, how is 'carpet area' defined, and does it include "
            "the area of the walls?"
        ),
        "expected_answer": (
            "Under Section 2(k) of RERA 2016, 'carpet area' means the net usable "
            "floor area of an apartment, excluding the area covered by the external "
            "walls, areas under service shafts, the exclusive balcony or verandah "
            "area, and exclusive open terrace area, but includes the area covered "
            "by the internal partition walls of the apartment. So internal partition "
            "walls are included; external walls are excluded."
        ),
        "source_citation": "RERA 2016, Section 2(k)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-09",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "A homebuyer has booked a flat and paid the booking amount. Under RERA "
            "2016, what is the maximum advance or deposit a promoter can accept from "
            "an allottee before the sale agreement is executed?"
        ),
        "expected_answer": (
            "Under the proviso to Section 13(1) of RERA 2016, a promoter cannot "
            "accept more than 10% of the cost of the apartment, plot or building "
            "as an advance payment or application fee before entering into a written "
            "agreement for sale with the allottee and registering it."
        ),
        "source_citation": "RERA 2016, Section 13(1) proviso",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T1-10",
        "tier":          1,
        "tier_name":     "Factual Recall",
        "question_text": (
            "Under RERA 2016, within how many days after receiving the occupancy "
            "certificate must a promoter hand over possession of the apartment to "
            "the allottee?"
        ),
        "expected_answer": (
            "RERA 2016 does not prescribe a fixed number of days after the "
            "occupancy certificate for handing over possession. The possession date "
            "is governed by the agreement for sale (Section 11(4)(a)). The promoter "
            "must offer possession in accordance with that agreement. Section 18 "
            "provides that if the promoter fails to give possession by the agreed "
            "date, the allottee is entitled to a refund with interest or to continue "
            "in the project with interest for the delay period."
        ),
        "source_citation": "RERA 2016, Sections 11(4)(a) and 18",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },

    # ── T2: Applied Compliance (10Q) ──────────────────────────────────────────
    {
        "question_id":   "V2-T2-01",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A promoter has collected Rs 5 crore from allottees for a residential "
            "project. How much must be deposited in the RERA-designated separate "
            "account, and can the promoter use the remaining 30% for marketing?"
        ),
        "expected_answer": (
            "Rs 3.5 crore (70% of Rs 5 crore) must be deposited in the separate "
            "account under Section 4(2)(l)(D). The remaining 30% (Rs 1.5 crore) is "
            "not formally restricted by RERA and can be used for other purposes "
            "including marketing. However, withdrawals from the 70% account are "
            "permitted only in proportion to the percentage of project completion, "
            "and only after certification by an engineer, architect, and CA."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(D)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-02",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A homebuyer paid Rs 80 lakh for a 2BHK flat. The developer delayed "
            "possession by 18 months beyond the agreed date. The homebuyer now "
            "wants a full refund. Under RERA 2016, is the homebuyer entitled to a "
            "refund and what interest applies?"
        ),
        "expected_answer": (
            "Yes. Under Section 18(1)(a) of RERA 2016, if the promoter fails to "
            "complete or hand over possession by the agreed date, the allottee is "
            "entitled to withdraw from the project and receive a refund of the "
            "entire amount paid along with interest at the rate prescribed (SBI "
            "MCLR + 2% under most state rules, compounded monthly). The interest "
            "runs from the date of payment to the date of refund."
        ),
        "source_citation": "RERA 2016, Section 18(1)(a); state-specific rules on interest rate",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-03",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A promoter wants to make a structural change to the layout plan of a "
            "registered project — specifically, converting two 3BHK units into "
            "three 2BHK units on the same floor. Can this be done under RERA 2016 "
            "without consent of the allottees?"
        ),
        "expected_answer": (
            "No. Under Section 14(2) of RERA 2016, a promoter cannot make any "
            "additions or alterations in the sanctioned plans or specifications of "
            "the apartment, plot or building, or the common areas, without the "
            "previous written consent of at least two-thirds of the allottees. "
            "Minor additions or alterations as may be permitted by state rules may "
            "be an exception, but converting unit configurations on a floor is a "
            "material change requiring consent."
        ),
        "source_citation": "RERA 2016, Section 14(2)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-04",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A real estate agent facilitated the sale of apartments in a project "
            "that was not registered under RERA 2016. What penalty does the agent "
            "face under RERA?"
        ),
        "expected_answer": (
            "Under Section 62 of RERA 2016, a real estate agent who facilitates "
            "the sale or purchase in a project that is required to be but is not "
            "registered under RERA is liable to a penalty of Rs 10,000 per day "
            "for each day of default, which may cumulatively extend up to 5% of "
            "the cost of the plot, apartment or building for which the sale has "
            "been facilitated."
        ),
        "source_citation": "RERA 2016, Section 62",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-05",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A homebuyer notices that the actual carpet area of the delivered flat "
            "is 3% less than what was mentioned in the sale agreement. Under RERA "
            "2016, what are the homebuyer's rights?"
        ),
        "expected_answer": (
            "Under Section 14(3) of RERA 2016, where the actual carpet area is "
            "less than what was agreed, the promoter must refund the proportionate "
            "amount paid for the reduced area along with interest at the prescribed "
            "rate. If the reduction exceeds the minor variation limit set by state "
            "rules, the allottee may also have the right to withdraw. A 3% variance "
            "may or may not exceed the permissible limit depending on the applicable "
            "state rules."
        ),
        "source_citation": "RERA 2016, Section 14(3)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-06",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A promoter wants to extend the project completion date registered with "
            "RERA. The promoter claims force majeure due to COVID-related delays. "
            "What is the process under RERA 2016 for getting a project extension?"
        ),
        "expected_answer": (
            "Under Section 6 of RERA 2016, the registration of a project may be "
            "extended by the Authority on an application made by the promoter, for "
            "a period not exceeding one year in cases of force majeure (war, flood, "
            "drought, fire, cyclone, earthquake, or any other calamity caused by "
            "nature). The promoter must apply before the registration lapses. The "
            "Authority may grant extension after examining the reasons. During "
            "COVID, many state authorities issued suo motu extensions under "
            "government orders, but the standard process requires an application "
            "citing specific grounds."
        ),
        "source_citation": "RERA 2016, Section 6",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-07",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A homebuyer purchased a flat for Rs 60 lakh. The promoter is now "
            "insolvent and has abandoned the project. The homebuyer wants to "
            "approach RERA. Can RERA provide relief, and what options does the "
            "homebuyer have?"
        ),
        "expected_answer": (
            "RERA provides for the Authority to intervene under Section 8 when a "
            "promoter's registration is revoked — the Authority may facilitate "
            "completion of the project by another promoter or through the "
            "association of allottees. For insolvency, the IBC (Insolvency and "
            "Bankruptcy Code) overrides and allottees are treated as financial "
            "creditors. The homebuyer should file a claim before the Resolution "
            "Professional or liquidator under IBC, and can also approach RERA for "
            "execution of any existing orders. RERA and IBC run on parallel tracks; "
            "the NCLAT / Supreme Court has held RERA remedies are not barred by IBC."
        ),
        "source_citation": "RERA 2016, Section 8; IBC 2016, Section 5(8)(f)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-08",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A promoter wants to mortgage the land of the project to raise "
            "construction finance after RERA registration. Is this permitted under "
            "RERA 2016, and what condition must the promoter satisfy?"
        ),
        "expected_answer": (
            "Under Section 11(4)(h) of RERA 2016, a promoter must not mortgage or "
            "create a charge on the apartment, plot or building in a manner that "
            "would affect the right and interest of the allottee after the allottee "
            "has entered into an agreement for sale. For pre-sale mortgages to raise "
            "construction finance, the promoter must ensure that if a lien exists "
            "on the land, the loan proceeds are used only for the project, and the "
            "promoter must discharge the mortgage before handing over possession to "
            "allottees. RERA does not absolutely prohibit mortgaging, but requires "
            "that it not prejudice allottees."
        ),
        "source_citation": "RERA 2016, Section 11(4)(h)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-09",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "An allottee received possession of a flat in a RERA-registered project "
            "2 years ago. Now cracks have appeared in the structure. Under RERA "
            "2016, is the promoter obligated to repair this, and for how long?"
        ),
        "expected_answer": (
            "Under Section 14(3) of RERA 2016, the promoter is liable for any "
            "structural defect or any other defect in workmanship, quality or "
            "provision of services or any other obligations as per the agreement for "
            "sale for a period of 5 years from the date of handing over possession. "
            "The promoter must rectify such defects within 30 days of receiving "
            "written intimation. Since possession was 2 years ago, the allottee is "
            "within the 5-year defect liability period and can demand repair."
        ),
        "source_citation": "RERA 2016, Section 14(3)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T2-10",
        "tier":          2,
        "tier_name":     "Applied Compliance",
        "question_text": (
            "A promoter is running three phases of the same township project. "
            "Can the promoter register all three phases under a single RERA "
            "registration, and how are the 70% account obligations calculated?"
        ),
        "expected_answer": (
            "Under Section 4(2)(l)(D) of RERA 2016 and most state rules, each "
            "phase of a project must be registered separately with RERA. Each "
            "phase has its own registration number. The 70% designated account "
            "obligation applies per phase — 70% of amounts collected from allottees "
            "of each phase must be held in a separate account for that phase and "
            "used only for land and construction costs of that specific phase. "
            "Amounts from one phase cannot be used for another."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(D); state rules on phased registration",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },

    # ── T3: Procedural Knowledge (10Q) ────────────────────────────────────────
    {
        "question_id":   "V2-T3-01",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "Walk through the step-by-step process a promoter must follow to "
            "register a new residential project under RERA 2016, from preparing "
            "documents to receiving the registration certificate."
        ),
        "expected_answer": (
            "Step 1: Prepare all documents required under Section 4 — title deed / "
            "ownership proof, sanctioned plan, layout plan, project specifications, "
            "proforma of allotment letter and sale agreement, name and address of "
            "structural engineer, CA-certified project cost estimate. "
            "Step 2: Apply online through the state RERA portal at least 3 months "
            "before advertising (Section 3). Step 3: Pay the prescribed registration "
            "fee (varies by state and project size). Step 4: Authority may seek "
            "additional information within 15 days. Step 5: If application is "
            "complete, Authority grants registration within 30 days; if no order "
            "within 30 days, registration is deemed granted (Section 5). "
            "Step 6: Receive registration number, which must be quoted in all "
            "advertisements."
        ),
        "source_citation": "RERA 2016, Sections 3, 4, 5",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-02",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "What is the process for a homebuyer to file a complaint against a "
            "promoter under RERA 2016? Mention the forum, format, fee, and "
            "timeline for a decision."
        ),
        "expected_answer": (
            "Under Section 31 of RERA 2016: (1) Any aggrieved person may file a "
            "complaint with the RERA Authority against a promoter, allottee, or "
            "real estate agent. (2) Complaint must be filed on the prescribed form "
            "through the state RERA portal with the prescribed fee. (3) The "
            "Authority adjudicates the complaint and can pass directions, impose "
            "penalties, or refer to Adjudicating Officer for compensation. "
            "The Adjudicating Officer must decide within 60 days (Section 71(5)) "
            "and must record reasons for delay. For compensation specifically, "
            "the Adjudicating Officer has jurisdiction under Section 71."
        ),
        "source_citation": "RERA 2016, Sections 31, 71",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-03",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "A promoter's RERA registration has lapsed. What steps must the "
            "promoter take to revive or extend the registration, and what happens "
            "to ongoing sales if the registration is not renewed in time?"
        ),
        "expected_answer": (
            "Under Section 6 of RERA 2016, the promoter must apply for extension "
            "before the registration lapses, citing force majeure or other grounds "
            "accepted by the Authority. The Authority may grant up to one year's "
            "extension. If registration lapses without renewal, the promoter cannot "
            "advertise, sell or accept new bookings. Under Section 7, if the "
            "promoter fails to comply with RERA requirements or the registration "
            "is revoked, the Authority can take over the project, and ongoing "
            "allottees retain their rights. New sales after lapse expose the "
            "promoter to penalty under Section 59."
        ),
        "source_citation": "RERA 2016, Sections 6, 7, 59",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-04",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "Explain the process by which a promoter can withdraw funds from the "
            "RERA-designated 70% account for construction expenses. Who must "
            "certify withdrawals and at what stage?"
        ),
        "expected_answer": (
            "Under Section 4(2)(l)(D) of RERA 2016, withdrawals from the separate "
            "designated account must be in proportion to the percentage of "
            "completion of the project. Each withdrawal requires a certificate "
            "from an engineer, an architect, and a chartered accountant (CA) in "
            "practice confirming that the withdrawal is proportionate to the work "
            "completed. This triple-certification requirement is mandatory — "
            "without it, the withdrawal is in violation of RERA and exposes the "
            "promoter to penalty under Section 61."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(D)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-05",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "What disclosures must a promoter make on the RERA website when "
            "registering a project? List the key categories of information "
            "required under Section 4 and Section 11."
        ),
        "expected_answer": (
            "Under Sections 4 and 11, the promoter must disclose: (1) Details of "
            "the enterprise — name, address, type of enterprise. (2) Particulars "
            "of the land — brief details, extent, title, encumbrances. "
            "(3) Layout and construction plans sanctioned by competent authority. "
            "(4) Specifications of the apartment, plot or building. "
            "(5) Proposed facilities — amenities and services. (6) Number and "
            "type of apartments and their carpet areas. (7) Timeline for completion "
            "per stage. (8) Proforma of allotment letter, agreement for sale, "
            "and conveyance deed. (9) Names and addresses of real estate agents "
            "authorised. (10) Names and addresses of contractors, architects, "
            "structural engineers. (11) Quarterly updates on project progress. "
            "(12) Annual audit report of the designated account."
        ),
        "source_citation": "RERA 2016, Sections 4(2), 11(1)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-06",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "Describe the process and legal requirements for executing a valid "
            "agreement for sale under RERA 2016, including when it must be executed "
            "and what it must contain."
        ),
        "expected_answer": (
            "Under Section 13 of RERA 2016: (1) The promoter must not accept more "
            "than 10% of the cost as advance before executing the agreement for "
            "sale. (2) The agreement must be in writing, on the terms prescribed "
            "by the state rules, and must be registered. (3) The agreement must "
            "include: exact carpet area and price per sq ft, floor plan, payment "
            "schedule, possession date, specifications, amenities promised, penalty "
            "for delay by either party, and provision for refund. (4) Any "
            "agreement clause that reduces the promoter's liability below the RERA "
            "standard is void (Section 15 principle). State rules (e.g., Karnataka "
            "RERA Rules 2017) prescribe the model agreement format."
        ),
        "source_citation": "RERA 2016, Section 13; state rules model agreement",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-07",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "How does a promoter transfer a RERA-registered project to a new "
            "promoter (say, due to change in ownership of the developer company)? "
            "What approvals are required under RERA 2016?"
        ),
        "expected_answer": (
            "Under Section 15 of RERA 2016, a promoter cannot transfer or assign "
            "majority rights and liabilities in respect of a real estate project "
            "to a third party without obtaining the prior written consent of "
            "two-thirds of the allottees and the prior approval of the RERA "
            "Authority. The transferee promoter takes on all obligations of the "
            "original promoter under RERA. Any transfer without this consent "
            "and approval is invalid."
        ),
        "source_citation": "RERA 2016, Section 15",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-08",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "What is the process for formation of a Residents' Welfare Association "
            "(RWA) or Resident Association under RERA 2016, and when is the "
            "promoter obligated to facilitate this?"
        ),
        "expected_answer": (
            "Under Section 11(4)(e) of RERA 2016, the promoter must enable the "
            "formation of an association, society, or cooperative society (by "
            "whatever name called) of the allottees within 3 months of the "
            "majority of allottees having booked their apartments. Where the "
            "applicable local laws require registration under a specific Act "
            "(e.g., Societies Registration Act), the promoter must facilitate "
            "that registration. The promoter must transfer all relevant documents, "
            "common area title, and project records to this association "
            "upon completion of the project."
        ),
        "source_citation": "RERA 2016, Section 11(4)(e)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-09",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "A homebuyer wants to cancel the booking and get a refund before "
            "possession. Describe the RERA process for such a cancellation, "
            "including the rights of both the allottee and the promoter."
        ),
        "expected_answer": (
            "Under Section 11(4)(d) and Section 18 of RERA 2016: (1) If the "
            "cancellation is initiated by the allottee (voluntary withdrawal), "
            "the rights depend on the agreement for sale — the promoter may "
            "forfeit the booking amount or cancellation charges as per the "
            "agreement, subject to state rules capping such deductions. "
            "(2) If the cancellation is due to default or delay by the promoter, "
            "the allottee is entitled to a full refund with interest (Section 18). "
            "(3) The promoter cannot unilaterally cancel the allotment without "
            "giving notice and an opportunity to cure under Section 11(4)(d) — "
            "notice must be given in writing and in the prescribed manner."
        ),
        "source_citation": "RERA 2016, Sections 11(4)(d), 18",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T3-10",
        "tier":          3,
        "tier_name":     "Procedural Knowledge",
        "question_text": (
            "Outline the quarterly update obligations of a registered promoter "
            "on the RERA portal. What information must be updated and what are "
            "the consequences of non-compliance?"
        ),
        "expected_answer": (
            "Under Section 11(1) of RERA 2016, every registered promoter must "
            "update the RERA website each quarter with: (a) current project status "
            "and construction progress; (b) unsold inventory and details of units "
            "sold; (c) any change in key project details. Non-compliance with "
            "disclosure obligations attracts penalty under Section 61 — up to 5% "
            "of the estimated cost of the project per default. Persistent "
            "non-compliance can be grounds for revocation of registration "
            "under Section 7."
        ),
        "source_citation": "RERA 2016, Sections 11(1), 61, 7",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },

    # ── T4: Multi-hop Reasoning (10Q) ─────────────────────────────────────────
    {
        "question_id":   "V2-T4-01",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A homebuyer paid Rs 1.2 crore for a flat. The promoter was delayed "
            "24 months from the agreed possession date, and the buyer continued "
            "in the project (did not withdraw). The buyer now wants compensation "
            "for the 24-month delay. Under RERA, (a) what interest rate applies, "
            "(b) on what amount is interest calculated, and (c) which section "
            "governs, and what is the total interest payable if the state rules "
            "set the rate at SBI MCLR + 2% (assume MCLR = 8.5%)?"
        ),
        "expected_answer": (
            "Under Section 18(1)(b) of RERA 2016, an allottee who does not "
            "withdraw is entitled to interest for every month of delay. "
            "(a) The rate under most state rules is SBI MCLR + 2% per annum = "
            "10.5% p.a. in this scenario. "
            "(b) Interest is on the amount paid by the allottee to the promoter "
            "— Rs 1.2 crore. "
            "(c) Section 18(1)(b) governs. "
            "Total interest = Rs 1.2 crore x 10.5% x (24/12) = Rs 1.2 crore x "
            "0.105 x 2 = Rs 25.2 lakh (simple interest basis; some states compound "
            "monthly, which would be higher). The buyer should file a complaint "
            "with the RERA Authority or Adjudicating Officer."
        ),
        "source_citation": "RERA 2016, Section 18(1)(b); state rules on interest rate",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-02",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A promoter launches a project with 120 apartments. After 60 are sold "
            "and agreements registered, the promoter wants to change the design of "
            "the common areas — specifically to convert the planned swimming pool "
            "into a clubhouse. What thresholds must be crossed for consent, who "
            "gives it, and which section applies? Does the same threshold apply "
            "to changes in a sold individual apartment?"
        ),
        "expected_answer": (
            "Under Section 14(2) of RERA 2016, any addition or alteration in "
            "sanctioned plans or specifications including common areas requires "
            "written consent of at least two-thirds of the allottees. With 60 "
            "allottees, the promoter needs consent from at least 40 allottees "
            "(two-thirds of 60). The same two-thirds threshold applies to changes "
            "in common areas. For changes to an individual apartment that has been "
            "allotted, the promoter needs the prior written consent of the specific "
            "allottee of that apartment — not two-thirds. The two-thirds requirement "
            "is specific to common areas and overall project plan changes."
        ),
        "source_citation": "RERA 2016, Section 14(2)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-03",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A promoter's project is registered with RERA and has 100 allottees. "
            "The promoter now becomes insolvent and NCLT admits an insolvency "
            "petition. The allottees want to simultaneously pursue their RERA "
            "complaint and the IBC process. Can they? What is the legal position "
            "post the Supreme Court's key rulings on RERA-IBC interaction?"
        ),
        "expected_answer": (
            "Post the Supreme Court's ruling in Pioneer Urban Land v Union of India "
            "(2019) and subsequent NCLAT decisions, allottees are classified as "
            "financial creditors under IBC Section 5(8)(f) and can file for "
            "insolvency. The Supreme Court held that allottees can pursue either "
            "RERA remedies or IBC, but once NCLT admits an insolvency petition, "
            "the moratorium under Section 14 of IBC kicks in and RERA proceedings "
            "against the corporate debtor are stayed. The REAT can still issue "
            "orders but execution is stayed. After the moratorium, allottees "
            "participate in the resolution process as financial creditors. So "
            "simultaneous pursuit is possible before NCLT admission; after "
            "admission, RERA execution is effectively paused."
        ),
        "source_citation": "RERA 2016 & IBC 2016, Section 5(8)(f); Pioneer Urban Land v UoI (2019)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-04",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A promoter signed sale agreements with 200 allottees and collected "
            "Rs 40 crore. The promoter then withdrew Rs 15 crore from the "
            "designated account without the required certifications. The project "
            "is 20% complete. What are the legal implications for the promoter, "
            "and what recourse do allottees have under RERA?"
        ),
        "expected_answer": (
            "The designated account must hold 70% of amounts collected = Rs 28 "
            "crore. The promoter may withdraw proportionate to completion — 20% "
            "of Rs 28 crore = Rs 5.6 crore without certifications is permissible. "
            "Withdrawing Rs 15 crore without triple-certification (engineer, "
            "architect, CA) is a violation of Section 4(2)(l)(D). The penalty is "
            "under Section 61 — up to 5% of project cost per violation. "
            "Allottees can file a complaint under Section 31 with the RERA "
            "Authority. The Authority can also revoke the promoter's registration "
            "under Section 7 if the violation is persistent. The excess withdrawal "
            "(Rs 15 crore vs permissible Rs 5.6 crore = Rs 9.4 crore excess) "
            "may need to be restored to the account."
        ),
        "source_citation": "RERA 2016, Sections 4(2)(l)(D), 7, 31, 61",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-05",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A homebuyer purchased a flat primarily on the basis of a brochure "
            "showing a clubhouse and rooftop garden as 'amenities included'. "
            "These features are not mentioned in the RERA registration or the "
            "agreement for sale. The project is delivered without these features. "
            "What legal remedies does the homebuyer have, and under which sections?"
        ),
        "expected_answer": (
            "Under Section 12 of RERA 2016, if a person acts on any false "
            "information or advertisement of a promoter and thereby suffers loss, "
            "the promoter is liable to pay compensation to the allottee. Under "
            "Section 14(1), the promoter is bound to deliver what is in the "
            "sanctioned plan and agreement. If the clubhouse and rooftop garden "
            "were advertised but not in the agreement, the homebuyer has a claim "
            "under Section 12 for misrepresentation and Section 14 for shortfall "
            "in promised specifications. The homebuyer can file a complaint before "
            "the Adjudicating Officer for compensation under Section 71. Consumer "
            "courts under the Consumer Protection Act may also have parallel "
            "jurisdiction for deficiency in service."
        ),
        "source_citation": "RERA 2016, Sections 12, 14(1), 71",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-06",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A plot buyer in a plotted development scheme wants to know if RERA "
            "applies. The layout has 50 plots, each above 500 sq metres. The "
            "promoter says RERA does not apply because plots are not 'apartments'. "
            "Is the promoter correct? Reason through the RERA definitions and "
            "scope provisions."
        ),
        "expected_answer": (
            "No, the promoter is incorrect. Under Section 2(j) of RERA 2016, "
            "'real estate project' includes development of land into plots. "
            "Section 3 makes RERA registration mandatory for real estate projects "
            "including plotted development. The exemption under Section 3(2)(a) "
            "applies only if the plot of land does not exceed 500 sq metres — here "
            "individual plots are above 500 sq metres, but the threshold refers "
            "to the total project land, not individual plot size. If the total "
            "layout land exceeds 500 sq metres (50 plots, each above 500 sq m "
            "means total land is very large), registration is mandatory. 'Apartment' "
            "is not the trigger — Section 3 covers all real estate projects "
            "including plotted developments."
        ),
        "source_citation": "RERA 2016, Sections 2(j), 2(k), 3",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Promoter's false claim that RERA applies only to apartments",
    },
    {
        "question_id":   "V2-T4-07",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A homebuyer's sale agreement contains a clause saying that in case "
            "of any dispute, the promoter's decision shall be final and the allottee "
            "waives all rights to approach any court or authority. Under RERA 2016, "
            "is this clause enforceable? What principle governs, and what can the "
            "homebuyer do?"
        ),
        "expected_answer": (
            "Under Section 89 of RERA 2016, the provisions of RERA have effect "
            "notwithstanding anything inconsistent in any other law. Any contract "
            "clause that attempts to oust the jurisdiction of the RERA Authority, "
            "Adjudicating Officer, or Appellate Tribunal is void as being contrary "
            "to public policy and inconsistent with RERA's mandatory provisions. "
            "The homebuyer retains full rights under RERA irrespective of such "
            "a waiver clause. The homebuyer can file a complaint under Section 31 "
            "with the RERA Authority, and the waiver clause in the agreement "
            "cannot be used to defeat that right."
        ),
        "source_citation": "RERA 2016, Sections 31, 89",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-08",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "RERA was enacted in 2016 and consumer protection cases for real estate "
            "were previously filed under the Consumer Protection Act (CPA). After "
            "RERA, can a homebuyer still file a complaint under CPA for deficiency "
            "in service by a promoter, or is RERA the exclusive remedy?"
        ),
        "expected_answer": (
            "The Supreme Court in Fortune Infrastructure v Trevor D'Lima (2018) "
            "and subsequent cases has held that RERA and CPA provide concurrent "
            "remedies — the homebuyer is not restricted to RERA alone. RERA does "
            "not explicitly bar CPA jurisdiction. Section 79 of RERA bars civil "
            "courts from entertaining suits that RERA covers, but Consumer Forums "
            "under CPA are not civil courts and are not barred. However, a "
            "homebuyer cannot seek the same relief through both forums "
            "simultaneously (double recovery is not permitted). The homebuyer "
            "must elect one forum for executing any award."
        ),
        "source_citation": "RERA 2016, Section 79; Consumer Protection Act; SC rulings",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },
    {
        "question_id":   "V2-T4-09",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "A promoter claims that since state rules have not been notified for "
            "their state, RERA does not apply and they are not required to register. "
            "Is this argument valid under the central RERA 2016?"
        ),
        "expected_answer": (
            "No, this argument is not valid. Section 84 of RERA 2016 requires "
            "states to make rules within 6 months of commencement. Section 85 "
            "provides that if a state fails to make rules within that period, "
            "the central government may make rules for that state. Furthermore, "
            "RERA itself (as central legislation) is in force in all states from "
            "the date of commencement. The absence of state rules does not suspend "
            "the obligation to register — the central provisions, including "
            "Section 3's mandatory registration, apply directly. The promoter "
            "is required to register with the state RERA authority established "
            "under Section 20, which must be set up by the state."
        ),
        "source_citation": "RERA 2016, Sections 3, 20, 84, 85",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Promoter's false claim that absent state rules = RERA not applicable",
    },
    {
        "question_id":   "V2-T4-10",
        "tier":          4,
        "tier_name":     "Multi-hop Reasoning",
        "question_text": (
            "An allottee wants to sell their under-construction flat (not yet "
            "handed over) to a third party. Under RERA 2016, what is required "
            "for such a transfer, and does the new buyer get the same rights "
            "as the original allottee?"
        ),
        "expected_answer": (
            "Under Section 15 of RERA 2016, an allottee may transfer their rights "
            "and obligations to another person in accordance with the agreement "
            "for sale. Such transfer requires the written consent of the promoter. "
            "The new allottee (transferee) steps into the shoes of the original "
            "allottee and acquires all rights and obligations under RERA and "
            "the agreement for sale — including the right to demand possession by "
            "the agreed date, the right to interest for delay under Section 18, "
            "the right to defect liability under Section 14(3), and the right to "
            "file a complaint under Section 31. The promoter cannot impose "
            "additional charges or restrictions beyond what is in the agreement."
        ),
        "source_citation": "RERA 2016, Section 15",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "",
    },

    # ── T5: Adversarial / Trap (10Q) ──────────────────────────────────────────
    {
        "question_id":   "V2-T5-01",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter claims: 'Our project has only 7 apartments across 600 sq "
            "metres of plot area. We are exempt from RERA registration because "
            "the number of apartments is below 8.' Is the promoter correct?"
        ),
        "expected_answer": (
            "No. The promoter is wrong. Under Section 3(2)(a) and (b) of RERA 2016, "
            "a project is exempt from registration only if BOTH conditions are met "
            "simultaneously — OR alternatively depending on interpretation — but "
            "the Supreme Court and most state authorities have held these are "
            "alternative thresholds: exempt if EITHER (a) land does not exceed "
            "500 sq metres OR (b) number of apartments does not exceed 8. Here "
            "the land is 600 sq metres (exceeds 500) but apartments are 7 "
            "(below 8). Whether registration is required depends on whether the "
            "thresholds are cumulative (both must be met for exemption) or "
            "alternative (either suffices). The plain reading of Section 3(2) "
            "uses 'or' — so if either threshold is met, the project is exempt. "
            "With 7 apartments (below 8), the project may qualify for exemption "
            "under the 'or' reading, but this varies by state. The promoter "
            "should verify their specific state's interpretation."
        ),
        "source_citation": "RERA 2016, Section 3(2)(a) and (b)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Thresholds are alternative ('or'), not cumulative ('and') — most states",
    },
    {
        "question_id":   "V2-T5-02",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A homebuyer claims: 'The promoter must give me possession within "
            "30 days of receiving the occupancy certificate under RERA.' Is "
            "this correct? Cite the exact provision."
        ),
        "expected_answer": (
            "This claim is not entirely correct. RERA 2016 does not prescribe a "
            "universal 30-day post-OC possession period. The possession timeline "
            "is governed by the agreement for sale (Section 11(4)(a)) — whatever "
            "date is agreed in the sale agreement. RERA does require the promoter "
            "to offer possession as per that agreed date. The 30-day figure may "
            "appear in some state rules or specific agreements, but it is not a "
            "blanket central RERA provision. The homebuyer's right to interest "
            "for delay under Section 18 starts from the date agreed in the sale "
            "agreement, not 30 days after OC."
        ),
        "source_citation": "RERA 2016, Section 11(4)(a), Section 18",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "No universal 30-day post-OC rule in central RERA",
    },
    {
        "question_id":   "V2-T5-03",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter argues: 'Once I hand over possession and the homebuyer "
            "signs the possession letter, I have no further liability under RERA.' "
            "Is this correct?"
        ),
        "expected_answer": (
            "No. Under Section 14(3) of RERA 2016, the promoter remains liable "
            "for structural defects or any other defects in workmanship, quality "
            "or provision of services for 5 years from the date of handing over "
            "possession. Signing a possession letter does not waive this statutory "
            "liability. Any agreement clause purporting to waive this liability "
            "is void as being inconsistent with RERA (Section 89 overrides "
            "inconsistent contractual terms). The promoter must rectify defects "
            "within 30 days of intimation during this 5-year period."
        ),
        "source_citation": "RERA 2016, Sections 14(3), 89",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Post-possession 5-year defect liability cannot be waived by signing possession letter",
    },
    {
        "question_id":   "V2-T5-04",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A real estate agent claims: 'I only help buyers find properties; I "
            "do not facilitate the actual sale transaction, so I do not need to "
            "register under RERA.' Is this argument valid?"
        ),
        "expected_answer": (
            "No. Under Section 9 of RERA 2016, any person who acts as a real "
            "estate agent — negotiating or acting on behalf of a person in a "
            "transaction for sale or purchase of a plot, apartment or building "
            "in a registered real estate project — is required to register. "
            "Helping buyers find properties and introducing them to developers "
            "is 'acting as an agent' in a transaction. The distinction the agent "
            "draws (finding vs facilitating) is not recognised in RERA. The "
            "obligation to register applies regardless of whether the agent "
            "formally executes the sale document."
        ),
        "source_citation": "RERA 2016, Section 9",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Agent's false distinction between 'finding properties' and 'facilitating sale'",
    },
    {
        "question_id":   "V2-T5-05",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter claims: 'I can keep all amounts paid by allottees in the "
            "same account as my company's operating funds, as long as I maintain "
            "records showing 70% is earmarked for the project.' Is this compliant "
            "with RERA 2016?"
        ),
        "expected_answer": (
            "No. Section 4(2)(l)(D) of RERA 2016 explicitly requires a 'separate "
            "account' in a scheduled bank — it must be a distinct bank account, "
            "not a mental earmarking or accounting entry in a commingled account. "
            "The language 'separate account' means physical separation in a "
            "dedicated bank account. Keeping allottee funds in the company's "
            "operating account is a clear violation even if internal records "
            "'earmark' the funds, exposing the promoter to penalty under Section 61."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(D)",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Promoter falsely equates accounting earmark with separate bank account",
    },
    {
        "question_id":   "V2-T5-06",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A homebuyer's advocate argues: 'Under RERA, if the promoter delays "
            "possession, the homebuyer can claim both a full refund AND continued "
            "interest until actual refund AND compensation for mental agony — all "
            "three simultaneously.' Is this legally accurate under RERA 2016?"
        ),
        "expected_answer": (
            "Partially. Under Section 18(1)(a) of RERA, the homebuyer can claim "
            "a full refund of amounts paid plus interest (at the prescribed rate) "
            "from the date of payment to the date of actual refund — these two go "
            "together. For compensation beyond interest (such as for mental agony "
            "or consequential losses), the homebuyer must approach the Adjudicating "
            "Officer under Section 71, who can award compensation. However, the "
            "homebuyer cannot claim both interest on the refund AND the refund "
            "itself AND additional compensation as separate independent heads — the "
            "refund with interest is the primary remedy, and additional compensation "
            "requires a separate determination. The exact scope depends on "
            "individual case facts and state authority practice."
        ),
        "source_citation": "RERA 2016, Sections 18(1)(a), 71, 72",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Mental agony compensation requires Adjudicating Officer; not automatic under S.18",
    },
    {
        "question_id":   "V2-T5-07",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter says: 'Our project was under construction before RERA came "
            "into force in 2017, so we are not required to register because RERA "
            "cannot apply retrospectively to ongoing projects.' Is the promoter "
            "legally correct?"
        ),
        "expected_answer": (
            "No. Section 3 of RERA 2016, read with the provisos to Section 3 and "
            "the transitional provisions, explicitly requires all ongoing projects "
            "(projects that have not received the completion certificate or "
            "occupancy certificate as of the date RERA commenced in the state) "
            "to be registered within 3 months of the commencement of the Act "
            "in that state. RERA has prospective application to ongoing projects "
            "as a matter of regulatory reform — this is not retrospective "
            "application. The Supreme Court in various rulings has upheld this. "
            "Failure to register an ongoing project attracts penalty under Section 59."
        ),
        "source_citation": "RERA 2016, Section 3 proviso; transitional provisions",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Ongoing projects at RERA commencement are explicitly covered and must register",
    },
    {
        "question_id":   "V2-T5-08",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter's brochure advertises 'super built-up area' and charges "
            "the homebuyer on that basis. The sale agreement also uses super "
            "built-up area. The homebuyer complains this violates RERA. Is the "
            "homebuyer correct?"
        ),
        "expected_answer": (
            "Yes. Under Section 4(2)(l)(v) of RERA 2016, promoters are required "
            "to mandatorily quote carpet area in all correspondence, allotment "
            "letters, agreements for sale and in advertisements. Charging on the "
            "basis of super built-up area or any area other than carpet area is "
            "not permitted under RERA. The promoter must specify the actual carpet "
            "area and the price per sq ft on carpet area basis. Using super "
            "built-up area in the agreement is a violation and the homebuyer is "
            "correct to complain. The homebuyer can seek a rectification of the "
            "agreement and pricing on carpet area basis, and can file a complaint "
            "under Section 31 for misrepresentation."
        ),
        "source_citation": "RERA 2016, Section 4(2)(l)(v), Section 12",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Super built-up area billing was common pre-RERA but is not permitted under RERA",
    },
    {
        "question_id":   "V2-T5-09",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A homebuyer claims: 'The promoter has not updated the RERA portal "
            "for 6 months. This alone entitles me to withdraw from the project "
            "and get a full refund with interest under Section 18.' Is this claim "
            "correct under RERA 2016?"
        ),
        "expected_answer": (
            "Not directly. Section 18 of RERA 2016 grants refund rights specifically "
            "when the promoter (a) fails to complete or hand over possession by "
            "the agreed date, (b) fails to give possession due to discontinuance "
            "of business, or (c) fails to give possession for any other reason. "
            "Failure to update the portal is a separate violation under Section 11 "
            "and attracts penalty under Section 61, but it is not by itself a "
            "trigger for Section 18 refund rights. The homebuyer cannot withdraw "
            "and claim a refund solely on the basis of portal non-update — there "
            "must be an actual failure of possession or other substantive breach. "
            "The homebuyer can report the portal non-compliance to the Authority, "
            "which can impose penalties on the promoter."
        ),
        "source_citation": "RERA 2016, Sections 11, 18, 61",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Portal non-update is a penalty matter, not a Section 18 refund trigger",
    },
    {
        "question_id":   "V2-T5-10",
        "tier":          5,
        "tier_name":     "Adversarial / Trap",
        "question_text": (
            "A promoter argues: 'Since the homebuyer also defaulted on 2 EMI "
            "payments, we have the right to cancel the allotment immediately and "
            "forfeit all amounts paid, without any RERA restrictions.' Is this "
            "correct under RERA 2016?"
        ),
        "expected_answer": (
            "No. Under Section 11(4)(d) of RERA 2016 and most state rules, a "
            "promoter cannot unilaterally cancel an allotment for default without "
            "following the prescribed process — giving written notice to the "
            "allottee and providing a reasonable opportunity to cure the default "
            "(typically 15 days under most state rules before cancellation). "
            "Full forfeiture of all amounts paid is also not permitted — the "
            "promoter can only forfeit the booking amount or the amount prescribed "
            "by state rules as 'forfeiture charge'. Any forfeiture beyond the "
            "prescribed limit must be refunded with interest. The promoter's "
            "claim of unrestricted cancellation and total forfeiture is incorrect."
        ),
        "source_citation": "RERA 2016, Section 11(4)(d); state rules on cancellation notice",
        "source_url":      "https://rera.karnataka.gov.in/act",
        "known_trap":      "Promoter cannot cancel immediately or forfeit everything — notice required, forfeiture capped",
    },
]


def build_benchmark(out_path: Path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # ── README ─────────────────────────────────────────────────────────────────
    ws_readme = wb.active
    ws_readme.title = "README"
    readme_lines = [
        ("Indian Regulatory Compliance Intelligence Assistant", True),
        ("V2 Benchmark — RERA User Journey Questions", True),
        ("", False),
        ("50 questions covering the full homebuyer / promoter / agent journey under RERA 2016.", False),
        ("Tiers:", False),
        ("  T1 (Q01-10)  Factual Recall       — basic RERA facts and thresholds", False),
        ("  T2 (Q11-20)  Applied Compliance   — applying rules to real scenarios", False),
        ("  T3 (Q21-30)  Procedural Knowledge — step-by-step RERA processes", False),
        ("  T4 (Q31-40)  Multi-hop Reasoning  — connecting multiple provisions", False),
        ("  T5 (Q41-50)  Adversarial / Trap   — false premises and edge cases", False),
        ("", False),
        ("Scoring rubric (LLM-as-judge, max 8 pts per question per system):", False),
        ("  Factual Accuracy     0-4", False),
        ("  Completeness        0-2", False),
        ("  Confidence Calibration  0-2", False),
        ("", False),
        ("Systems evaluated:", False),
        ("  A  Base LLM   — gpt-5-nano, no retrieval", False),
        ("  B  Plain RAG  — gpt-5-nano + ChromaDB (improved per-doc cap)", False),
        ("  C  Agentic RAG — gpt-5-nano + query decomposition + multi-hop reflection", False),
        ("", False),
        ("Judge model: deepseek-ai/DeepSeek-V4.1-Flash (Together AI)", False),
    ]
    for text, bold in readme_lines:
        cell = ws_readme.cell(ws_readme.max_row + (0 if ws_readme.max_row == 1 else 0), 1, text)
        cell.font = Font(bold=bold, name="Calibri", size=10 if not bold else 11)
        ws_readme.append([text])
    ws_readme.column_dimensions["A"].width = 80

    # ── 1_Benchmark ───────────────────────────────────────────────────────────
    ws = wb.create_sheet("1_Benchmark")

    tier_fills = {
        1: PatternFill("solid", fgColor="FFF2CC"),
        2: PatternFill("solid", fgColor="E2EFDA"),
        3: PatternFill("solid", fgColor="DDEEFF"),
        4: PatternFill("solid", fgColor="FCE4D6"),
        5: PatternFill("solid", fgColor="F4CCCC"),
    }
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_font   = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
    hdr_align  = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r_font     = Font(name="Calibri", size=9)
    r_align    = Alignment(vertical="top", wrap_text=True)

    cols = [
        ("question_id",   14),
        ("tier",           6),
        ("tier_name",     20),
        ("question_text", 55),
        ("expected_answer", 65),
        ("source_citation", 40),
        ("source_url",    40),
        ("known_trap",    35),
    ]

    for ci, (name, width) in enumerate(cols, 1):
        cell = ws.cell(1, ci, name.replace("_", " ").title())
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = hdr_align
        cell.border = border
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"

    for ri, q in enumerate(QUESTIONS, 2):
        fill = tier_fills.get(q["tier"], PatternFill())
        for ci, (key, _) in enumerate(cols, 1):
            cell = ws.cell(ri, ci, q.get(key, ""))
            cell.font = r_font
            cell.fill = fill
            cell.alignment = r_align
            cell.border = border
        ws.row_dimensions[ri].height = 80

    wb.save(str(out_path))
    print(f"Saved {out_path}  ({len(QUESTIONS)} questions)")


if __name__ == "__main__":
    out = Path(__file__).parent / "Benchmark_RERA_V2_UserJourney_50Q.xlsx"
    build_benchmark(out)
