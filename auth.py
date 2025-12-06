from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from database import SessionLocal, UserModel, SessionToken
from membership import User, Tier

# Use pbkdf2_sha256 instead of bcrypt to avoid 72-byte password limit issues
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


# ------------------ DB Session Dependency ------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------ Password Helpers ------------------
def hash_password(password: str) -> str:
    # passlib will handle unicode + salting etc.; no 72-byte limit here
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ------------------ User / Session Helpers ------------------
def create_user(
    db: Session,
    email: str,
    password: str,
    tier: Tier = Tier.TIER2_SINGLE_AUTO,
) -> UserModel:
    email = email.strip().lower()
    existing = db.query(UserModel).filter(UserModel.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with that email already exists.",
        )

    user = UserModel(
        email=email,
        hashed_password=hash_password(password),
        tier=tier.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> UserModel | None:
    email = email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_session_token(db: Session, user_id: int) -> str:
    token = str(uuid4())
    session = SessionToken(user_id=user_id, token=token)
    db.add(session)
    db.commit()
    db.refresh(session)
    return token


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Read session cookie and return the current user.

    If no cookie or no valid session is found, we treat the user as
    an anonymous Tier3 user so your existing behavior still works
    (full access) until you're ready to lock it down.
    """
    token = request.cookies.get("ktbb_session")

    if not token:
        # anonymous, default Tier3 for now
        return User(
            id=0,
            email="anonymous@ktbb.local",
            tier=Tier.TIER3_MULTI_GPT,
        )

    session = (
        db.query(SessionToken)
        .filter(SessionToken.token == token)
        .first()
    )

    if not session:
        # invalid / expired token, treat as anonymous Tier3
        return User(
            id=0,
            email="anonymous@ktbb.local",
            tier=Tier.TIER3_MULTI_GPT,
        )

    user = db.query(UserModel).filter(UserModel.id == session.user_id).first()
    if not user:
        return User(
            id=0,
            email="anonymous@ktbb.local",
            tier=Tier.TIER3_MULTI_GPT,
        )

    return User(
        id=user.id,
        email=user.email,
        tier=Tier(user.tier),
    )


def delete_session(
    db: Session,
    token: str,
) -> None:
    if not token:
        return
    db.query(SessionToken).filter(SessionToken.token == token).delete()
    db.commit()
