"""
Agent mapping persistence — load and save agent-user mappings via SQLAlchemy.
"""

import logging
from typing import Dict

from .types import AgentUserMapping

logger = logging.getLogger("matrix_client.agent_user_manager")


class AgentMappingPersistenceMixin:
    """Persistence methods mixed into AgentUserManager."""

    async def load_existing_mappings(self):
        """Load existing agent-user mappings from database"""
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            db_mappings = db.get_all()

            for db_mapping in db_mappings:
                mapping_dict = db_mapping.to_dict()
                if "invitation_status" not in mapping_dict:
                    mapping_dict["invitation_status"] = None

                try:
                    from src.core.identity_storage import get_identity_service

                    identity = get_identity_service().get_by_agent_id(str(db_mapping.agent_id))
                    if identity is not None:
                        if getattr(identity, "display_name", None):
                            mapping_dict["agent_name"] = str(identity.display_name)
                        if getattr(identity, "mxid", None):
                            mapping_dict["matrix_user_id"] = str(identity.mxid)
                except Exception as identity_error:
                    logger.debug(f"Identity enrichment unavailable for {db_mapping.agent_id}: {identity_error}")

                agent_id = str(db_mapping.agent_id)
                self.mappings[agent_id] = AgentUserMapping(**mapping_dict)

            logger.info(f"Loaded {len(self.mappings)} existing agent-user mappings from database")
        except Exception as e:
            logger.error(f"Error loading mappings from database: {e}")
            logger.warning("Database is unavailable. Agent mappings will be empty until DB is restored.")

    async def save_mappings(self):
        """Save agent-user mappings to database"""
        try:
            from src.models.agent_mapping import AgentMappingDB, get_session_maker
            from src.models.agent_mapping import AgentMapping as DBAgentMapping, InvitationStatus
            from sqlalchemy.dialects.postgresql import insert

            Session = get_session_maker()
            session = Session()

            try:
                for agent_id, mapping in self.mappings.items():
                    stmt = insert(DBAgentMapping).values(
                        agent_id=mapping.agent_id,
                        agent_name=mapping.agent_name,
                        matrix_user_id=mapping.matrix_user_id,
                        matrix_password=mapping.matrix_password,
                        room_id=mapping.room_id,
                        room_created=mapping.room_created
                    ).on_conflict_do_update(
                        index_elements=['agent_id'],
                        set_={
                            'agent_name': mapping.agent_name,
                            'room_id': mapping.room_id,
                            'room_created': mapping.room_created
                        }
                    )
                    session.execute(stmt)

                    if mapping.invitation_status:
                        for invitee, status in mapping.invitation_status.items():
                            stmt = insert(InvitationStatus).values(
                                agent_id=agent_id,
                                invitee=invitee,
                                status=status
                            ).on_conflict_do_update(
                                index_elements=['agent_id', 'invitee'],
                                set_={'status': status}
                            )
                            session.execute(stmt)

                session.commit()
                logger.info(f"Saved {len(self.mappings)} agent-user mappings to database")

            except Exception as db_error:
                session.rollback()
                raise db_error
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error saving mappings to database: {e}")
            logger.warning("Failed to save mappings. Changes will be lost if not retried.")
