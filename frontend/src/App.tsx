import { Navigate, Route, Routes } from "react-router-dom";

import CheckoutCancelled from "./pages/CheckoutCancelled";
import CheckoutSuccess from "./pages/CheckoutSuccess";
import History from "./pages/History";
import Landing from "./pages/Landing";
import Questionnaire from "./pages/Questionnaire";
import Report from "./pages/Report";
import Start from "./pages/Start";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/start" element={<Start />} />
      <Route path="/questionnaire" element={<Questionnaire />} />
      <Route path="/report/:reportId" element={<Report />} />
      <Route path="/reports" element={<History />} />
      {/* Where Stripe sends the browser back to. The paths are the ones the server builds
          into the success and cancel URLs — see `backend/app/routes/payments.py`. */}
      <Route path="/checkout/success" element={<CheckoutSuccess />} />
      <Route path="/checkout/cancelled" element={<CheckoutCancelled />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
