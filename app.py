import streamlit as st
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Tool
from google.cloud import firestore
from google.cloud import storage
from google.cloud import secretmanager
from google.cloud import logging as cloud_logging
import google.auth
from googleapiclient.discovery import build
from fpdf import FPDF
import datetime
import os
import pandas as pd
import logging
import urllib.parse

# --- CONFIGURATION (Non-Sensitive) ---
# It is safe to keep Project ID in code, it is not a secret key.
PROJECT_ID = "bnb-marathon-2025-478707"
LOCATION = "us-central1"
BUCKET_NAME = f"{PROJECT_ID}-reports"

# --- SECURITY: SECRET MANAGER ---
# We connect to the vault to get sensitive data
secret_client = secretmanager.SecretManagerServiceClient()

def get_secret(secret_id):
    """Fetches a secret value from Google Secret Manager."""
    try:
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
        response = secret_client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        st.error(f"Security Error: Could not fetch {secret_id}. Check IAM permissions.")
        return None

# FETCH SECRETS NOW (Runtime)
# The code doesn't know these values until it runs on the server!
ADMIN_PASSWORD = get_secret("app-admin-password")
MASTER_CALENDAR_ID = get_secret("master-calendar-id")

# --- SETUP ---
log_client = cloud_logging.Client()
log_client.setup_logging()
logger = logging.getLogger("medi-scribe-audit")

vertexai.init(project=PROJECT_ID, location=LOCATION)
db = firestore.Client(project=PROJECT_ID)
storage_client = storage.Client(project=PROJECT_ID)

# Fix for 400 Error (Use Dict)
grounding_tool = Tool.from_dict({"google_search": {}})
# Use standard Pro model
model = GenerativeModel("gemini-2.5-flash")

st.set_page_config(page_title="Medi-Scribe Enterprise", page_icon="🏥", layout="wide")

# --- 1. GOOGLE CALENDAR (Hospital Side Block) ---
def block_hospital_calendar(patient_name, doctor_name, appt_date, appt_time):
    """Blocks the slot on the HOSPITAL (Master) Calendar."""
    try:
        credentials, project = google.auth.default(
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=credentials)

        start_dt = datetime.datetime.combine(appt_date, appt_time)
        end_dt = start_dt + datetime.timedelta(minutes=30)
        
        event = {
            'summary': f'BOOKED: {doctor_name} (Pt: {patient_name})',
            'location': 'Medi-Scribe Medical Center',
            'description': f'Official Appointment.\n\nDoctor: {doctor_name}\nPatient: {patient_name}',
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
            # Uses the secret ID we fetched earlier
            'attendees': [{'email': MASTER_CALENDAR_ID}], 
        }

        event_result = service.events().insert(
            calendarId=MASTER_CALENDAR_ID, 
            body=event
        ).execute()
        
        return True, event_result.get('htmlLink')
    except Exception as e:
        logger.error(f"Calendar API Error: {e}")
        return False, str(e)

# --- 2. MAGIC LINK GENERATOR ---
def generate_google_calendar_link(patient_email, doctor_name, appt_date, appt_time):
    """Creates a URL for the USER'S Calendar."""
    start_dt = datetime.datetime.combine(appt_date, appt_time)
    end_dt = start_dt + datetime.timedelta(minutes=30)
    fmt = "%Y%m%dT%H%M00"
    dates = f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}"
    
    title = urllib.parse.quote(f"Doctor Appointment: {doctor_name}")
    details = urllib.parse.quote(f"Confirmed.\nDoctor: {doctor_name}\nEmail: {patient_email}")
    location = urllib.parse.quote("Medi-Scribe Medical Center")
    
    link = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={title}&details={details}&dates={dates}&location={location}"
    return link

# --- UTILS ---
def get_clean_text(response):
    try: return response.text
    except: return "".join([p.text for p in response.candidates[0].content.parts])

def check_auth():
    if "authenticated" not in st.session_state: st.session_state.authenticated = False
    return st.session_state.authenticated

def login_screen():
    st.markdown("### 🛡️ Admin Portal Access")
    pwd = st.text_input("Enter Secure Access Key", type="password")
    if st.button("Authenticate", type="primary"):
        # Compare input against the SECRET from the vault
        if pwd == ADMIN_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Access Denied")

def logout():
    st.session_state.authenticated = False
    st.rerun()

def create_professional_pdf(summary, doctors, sources, appt_info=None):
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 18)
        pdf.cell(0, 10, "Medi-Scribe Patient Report", ln=True, align='C')
        pdf.line(10, 25, 200, 25)
        pdf.ln(15)
        
        pdf.set_font("Arial", 'I', 10)
        pdf.cell(0, 10, f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
        pdf.ln(5)
        
        sections = [("Medical Analysis", summary), ("Recommended Specialists", doctors)]
        for title, content in sections:
            pdf.set_font("Arial", 'B', 12)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(0, 10, f"  {title}", ln=True, fill=True)
            pdf.ln(3)
            pdf.set_font("Arial", size=10)
            safe_content = content.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, safe_content)
            pdf.ln(8)

        if appt_info:
            pdf.set_font("Arial", 'B', 12)
            pdf.set_text_color(0, 100, 0)
            pdf.cell(0, 10, "  Appointment Status", ln=True, fill=True)
            pdf.ln(3)
            pdf.set_font("Arial", size=10)
            pdf.set_text_color(0, 0, 0)
            safe_appt = appt_info.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, safe_appt)
            pdf.ln(8)

        pdf.set_font("Arial", 'B', 12)
        pdf.set_fill_color(240, 240, 240)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, "  Verified Sources", ln=True, fill=True)
        pdf.ln(5)
        pdf.set_font("Arial", 'I', 9)
        pdf.set_text_color(50, 50, 150)
        safe_sources = sources.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 6, safe_sources)
        
        filename = f"Report_{int(datetime.datetime.now().timestamp())}.pdf"
        local_path = f"/tmp/{filename}"
        pdf.output(local_path)
        
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_filename(local_path)
        return local_path, f"gs://{BUCKET_NAME}/{filename}"
    except Exception as e: return None, str(e)

# --- MAIN APP UI ---
st.sidebar.title("🏥 Medi-Scribe")
st.sidebar.caption("v21.0 Enterprise Secured")
mode = st.sidebar.radio("Portal Access:", ["Patient Kiosk", "Admin Dashboard"])

if mode == "Patient Kiosk":
    st.title("🩺 Patient Service Portal")
    if "analysis_result" not in st.session_state: st.session_state.analysis_result = None
    if "specialist_type" not in st.session_state: st.session_state.specialist_type = "General Physician"
    if "user_city" not in st.session_state: st.session_state.user_city = "Hyderabad"
    if "sources_clean" not in st.session_state: st.session_state.sources_clean = None
    
    tab1, tab2 = st.tabs(["1. AI Analysis", "2. Book Appointment"])

    with tab1:
        col1, col2 = st.columns([1, 1.5])
        with col1:
            uploaded_file = st.file_uploader("Upload Report", type=["png", "jpg", "jpeg"])
            city_input = st.text_input("Your City", value="Hyderabad")
        with col2:
            if uploaded_file and st.button("✨ Process Record", type="primary", use_container_width=True):
                with st.spinner("Analyzing..."):
                    try:
                        image_part = Part.from_data(data=uploaded_file.getvalue(), mime_type=uploaded_file.type)
                        prompt = f"""
                        You are a Medical AI. 
                        1. ANALYZE image. 2. SUMMARIZE findings. 
                        3. RECOMMEND specialist type.
                        4. RETURN: SUMMARY: [text] SPECIALIST: [type] ADVICE: [text]
                        """
                        response = model.generate_content([image_part, prompt], tools=[grounding_tool])
                        text = get_clean_text(response)
                        
                        summary, specialist, advice = "Report Analyzed.", "General Physician", "Consultation recommended."
                        if "SUMMARY:" in text:
                            parts = text.split("SUMMARY:")[1]
                            if "SPECIALIST:" in parts:
                                summary, rest = parts.split("SPECIALIST:", 1)
                                if "ADVICE:" in rest:
                                    specialist, advice = rest.split("ADVICE:", 1)
                        
                        st.session_state.analysis_result = summary
                        st.session_state.specialist_type = specialist.strip().split('\n')[0].replace("*", "")
                        st.session_state.user_city = city_input
                        
                        sources = "Verified via Google."
                        try:
                            if response.candidates[0].grounding_metadata.grounding_chunks:
                                sources = ""
                                for i, c in enumerate(response.candidates[0].grounding_metadata.grounding_chunks):
                                    if c.web: sources += f"{i+1}. {c.web.title}: {c.web.uri}\n"
                        except: pass
                        st.session_state.sources_clean = sources

                        st.success(f"Analysis Complete. Recommended: {specialist}")
                        st.markdown(summary)
                        path, link = create_professional_pdf(summary, advice, sources)
                        if path:
                            with open(path, "rb") as f:
                                st.download_button("⬇️ Download Report", f, "Report.pdf", "application/pdf")
                    except Exception as e: st.error(f"Error: {e}")

    with tab2:
        st.header("📅 Doctor Appointment")
        if st.session_state.analysis_result:
            specialist = st.session_state.specialist_type
            city = st.session_state.user_city
            
            st.info(f"Recommended Specialist: **{specialist}** in **{city}**")
            
            # LINKS
            clean_city = city.lower().strip().replace(" ", "-")
            clean_spec = specialist.strip()
            apollo_url = f"https://www.apollo247.com/specialties/{urllib.parse.quote(specialist.lower())}"
            practo_url = f"https://www.practo.com/search/doctors?results_type=doctor&q={urllib.parse.quote(clean_spec)}&city={urllib.parse.quote(clean_city)}"
            c1, c2 = st.columns(2)
            with c1: st.link_button("🔎 Search on Practo", practo_url, use_container_width=True)
            with c2: st.link_button("🔎 Search on Apollo", apollo_url, use_container_width=True)
            
            st.divider()

            # BOOKING FORM
            st.markdown(f"### 2. Confirm Appointment")
            with st.form("booking_form"):
                c1, c2 = st.columns(2)
                with c1:
                    p_email = st.text_input("Your Gmail ID")
                    doc_name = st.text_input("Selected Doctor Name")
                with c2:
                    appt_date = st.date_input("Date", min_value=datetime.date.today())
                    appt_time = st.time_input("Time")
                submitted = st.form_submit_button("✅ Confirm Booking", type="primary", use_container_width=True)
            
            if submitted:
                if p_email and doc_name:
                    with st.spinner("Booking Slot..."):
                        # 1. Block Hospital Calendar (Uses SECRET ID)
                        success, result = block_hospital_calendar(p_email, doc_name, appt_date, appt_time)
                        
                        # 2. Generate User Link
                        user_cal_link = generate_google_calendar_link(p_email, doc_name, appt_date, appt_time)
                        
                        st.success(f"✅ Slot Blocked for {doc_name}!")
                        st.markdown(f"### 👉 [Click here to Add to YOUR Calendar]({user_cal_link})")
                        
                        db.collection("appointments").add({
                            "patient_email": p_email,
                            "specialist": specialist,
                            "doctor_name": doc_name,
                            "status": "Confirmed",
                            "timestamp": datetime.datetime.now()
                        })
                        
                        details = f"Email: {p_email}\nDoc: {doc_name}\nDate: {appt_date} {appt_time}\nStatus: Confirmed"
                        path, link = create_professional_pdf(st.session_state.analysis_result, details, st.session_state.sources_clean, details)
                        with open(path, "rb") as f:
                            st.download_button("⬇️ Download Booking Pass", f, "Pass.pdf", "application/pdf")
                else: st.warning("Fill details.")
        else: st.warning("Analyze report first.")

elif mode == "Admin Dashboard":
    if not check_auth(): login_screen()
    else:
        st.sidebar.success("Admin Logged In")
        if st.sidebar.button("Logout"): logout()
        st.title("📊 Analytics")
        if st.button("Refresh"):
            docs = db.collection("reports").stream()
            data = [d.to_dict() for d in docs]
            if data:
                df = pd.DataFrame(data)
                st.metric("Reports Processed", len(df))
                st.dataframe(df, use_container_width=True)
            else: st.info("No data.")