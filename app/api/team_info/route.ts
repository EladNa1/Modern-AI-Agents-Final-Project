import { NextResponse } from "next/server";

export const runtime = "nodejs";

// TODO before submission: fill in group_batch_order_number and teammate emails.
export async function GET() {
  return NextResponse.json({
    group_batch_order_number: "TBD_TBD",
    team_name: "CheckMate",
    students: [
      { name: "Elad Nahalieli", email: "eladna97@gmail.com" },
      { name: "Shiri Haboob", email: "TODO@campus.technion.ac.il" },
      { name: "Yaron Mozes", email: "TODO@campus.technion.ac.il" },
    ],
  });
}
