import { NextResponse } from "next/server";

export const runtime = "nodejs";

// TODO: fill in real group/order number and teammate details before submission.
export async function GET() {
  return NextResponse.json({
    group_batch_order_number: "TBD_TBD",
    team_name: "CheckMate",
    students: [
      { name: "Elad Nahalieli", email: "eladna97@gmail.com" },
      { name: "Student B", email: "b@example.com" },
      { name: "Student C", email: "c@example.com" },
    ],
  });
}
