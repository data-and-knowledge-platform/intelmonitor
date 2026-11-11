// Subject names link to their page wherever they appear.
import { Link } from "@tanstack/react-router";
import { Fragment } from "react";

import type { Schema } from "@/lib/api/client";
import { subjectLink } from "@/lib/links";

type SubjectRef = Schema<"SubjectRef">;

/** A subject name as a link; `tag` renders it as a type-colored tag. */
export function SubjectLink({ subject, tag = false }: { subject: SubjectRef; tag?: boolean }) {
  return (
    <Link {...subjectLink(subject)} className={tag ? `tag ${subject.type}` : "subject-link"}>
      {subject.name}
    </Link>
  );
}

/** Subject names as links, separated by commas. */
export function SubjectLinks({ subjects }: { subjects: SubjectRef[] }) {
  return subjects.map((s, i) => (
    <Fragment key={s.id}>
      {i > 0 && ", "}
      <SubjectLink subject={s} />
    </Fragment>
  ));
}
