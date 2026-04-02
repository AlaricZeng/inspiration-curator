import { Navigate, Route, Routes } from "react-router-dom";
import Setup from "./pages/Setup";
import Today from "./pages/Today";
import Curate from "./pages/Curate";
import Gallery from "./pages/Gallery";
import TasteProfile from "./pages/TasteProfile";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/today" replace />} />
      <Route path="/setup" element={<Setup />} />
      <Route path="/today" element={<Today />} />
      <Route path="/curate" element={<Curate />} />
      <Route path="/gallery" element={<Gallery />} />
      <Route path="/taste-profile" element={<TasteProfile />} />
    </Routes>
  );
}
